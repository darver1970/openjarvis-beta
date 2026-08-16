"""Lokální wake-word klient pro J.A.R.V.I.S.; všechny soubory zůstávají na A:."""

from __future__ import annotations

import json
import logging
import os
import queue
import subprocess
import threading
import time
import wave
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable

import numpy as np
import sounddevice as sd
import soundfile as sf
from openwakeword.model import Model
from piper import PiperVoice

from gemini_live import GeminiLiveError, ask_gemini_live


ROOT = Path(__file__).resolve().parent
CONFIG_PATH = ROOT / "runtime" / "voice-config.json"
ROUTER_PATH = ROOT / "runtime" / "model-router.json"
SESSION_PATH = ROOT / "runtime" / "shared-session.json"
RULES_PATH = ROOT / "runtime" / "jarvis-rules.json"
VOICE_CONTROL_PATH = ROOT / "runtime" / "voice-control.json"
RUNTIME_DIR = ROOT / "runtime" / "voice"
HUD_EVENT_PATH = ROOT / "hud" / "voice-event.json"
VOICE_METER_PATH = ROOT / "runtime" / "voice-meter.json"
GEMINI_LIVE_STATE_PATH = ROOT / "runtime" / "gemini-live-state.json"
HUD_VOICE_DIR = ROOT / "hud" / "voice"
PIPER_MODEL_PATH = ROOT / "runtime" / "piper" / "cs_CZ-jirka-medium.onnx"
LOG_PATH = ROOT / "runtime" / "voice-client.log"
API_URL = "http://127.0.0.1:8000"


def load_config() -> dict[str, object]:
    """Načte lokální konfiguraci klienta."""
    return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))


def voice_is_enabled() -> bool:
    """Respektuje přepínač hlasu uložený lokálním HUDem."""
    try:
        return bool(json.loads(VOICE_CONTROL_PATH.read_text(encoding="utf-8")).get("enabled", True))
    except (OSError, json.JSONDecodeError):
        return True


def cancellation_requested() -> bool:
    """Vrátí příznak STOP nastavený z HUDu."""
    try:
        return bool(json.loads(VOICE_CONTROL_PATH.read_text(encoding="utf-8")).get("cancel_requested", False))
    except (OSError, json.JSONDecodeError):
        return False


def clear_cancellation() -> None:
    state = {"enabled": voice_is_enabled(), "cancel_requested": False, "manual_listen": False}
    VOICE_CONTROL_PATH.write_text(json.dumps(state), encoding="utf-8")


def gemini_live_available(config: dict[str, object]) -> bool:
    """Vrátí dostupnost online hlasu a respektuje ochrannou prodlevu po chybě."""
    try:
        state = json.loads(GEMINI_LIVE_STATE_PATH.read_text(encoding="utf-8"))
        retry_after = str(state.get("retry_after", ""))
        if retry_after and datetime.fromisoformat(retry_after) > datetime.now():
            return False
    except (OSError, json.JSONDecodeError, ValueError):
        pass
    return bool(config.get("gemini_live_preferred", True))


def record_gemini_live_state(succeeded: bool, config: dict[str, object]) -> None:
    """Zapíše pouze stav dostupnosti; API klíč ani obsah řeči se neukládají."""
    state: dict[str, object] = {"last_checked": datetime.now().isoformat(timespec="seconds")}
    if succeeded:
        state["status"] = "available"
    else:
        retry_seconds = max(60, int(config.get("gemini_live_retry_seconds", 300)))
        state.update({
            "status": "fallback_local",
            "retry_after": (datetime.now() + timedelta(seconds=retry_seconds)).isoformat(timespec="seconds"),
        })
    GEMINI_LIVE_STATE_PATH.write_text(json.dumps(state), encoding="utf-8")


def consume_manual_listen() -> bool:
    """Přečte jednorázový ruční požadavek z reaktoru a ihned jej spotřebuje."""
    try:
        state = json.loads(VOICE_CONTROL_PATH.read_text(encoding="utf-8"))
        if not state.get("manual_listen", False):
            return False
        state["manual_listen"] = False
        state["enabled"] = True
        state["cancel_requested"] = False
        VOICE_CONTROL_PATH.write_text(json.dumps(state), encoding="utf-8")
        return True
    except (OSError, json.JSONDecodeError):
        return False


def manual_listen_requested() -> bool:
    """Zjistí kliknutí na reaktor i během právě streamované odpovědi."""
    try:
        return bool(json.loads(VOICE_CONTROL_PATH.read_text(encoding="utf-8")).get("manual_listen", False))
    except (OSError, json.JSONDecodeError):
        return False


def publish(event_type: str, **payload: object) -> None:
    """Předá stav lokálnímu HUD; obsazený soubor nesmí zastavit hlasový modul."""
    event = {"id": datetime.now().isoformat(), "type": event_type, **payload}
    temporary_path = HUD_EVENT_PATH.with_name(f"{HUD_EVENT_PATH.stem}-{os.getpid()}.tmp")
    try:
        temporary_path.write_text(json.dumps(event, ensure_ascii=False), encoding="utf-8")
        for attempt in range(3):
            try:
                os.replace(temporary_path, HUD_EVENT_PATH)
                return
            except PermissionError:
                if attempt == 2:
                    raise
                time.sleep(0.03)
    except OSError as error:
        logging.debug("HUD hlasovou událost nelze právě uložit: %s", error)
        try:
            temporary_path.unlink(missing_ok=True)
        except OSError:
            pass


def strip_wake_phrase(command: str) -> str:
    """Odstraní aktivační slovo z celé věty vyslovené bez další prodlevy."""
    import re

    return re.sub(
        r"^\s*(?:(?:hey|hej)\s+)?(?:jarvis(?:i|e)?|jarvi|j[áa]\s*[wv]i[sz]i|jar\s*vysi)[\s,.:!\-]*",
        "",
        command,
        flags=re.IGNORECASE,
    ).strip()


def contains_wake_phrase(command: str) -> bool:
    """Rozpozná české i anglické oslovení v přepisu lokálního Whisperu."""
    import re

    return bool(re.search(
        r"\b(?:(?:hey|hej)\s+)?(?:jarvis(?:i|e)?|jarvi|j[áa]\s*[wv]i[sz]i|jar\s*vysi)\b",
        command,
        flags=re.IGNORECASE,
    ))


def publish_meter(level: float) -> None:
    """Předá úroveň mikrofonu do HUDu; selhání měřiče nesmí zastavit řeč."""
    temporary_path = VOICE_METER_PATH.with_name(
        f"{VOICE_METER_PATH.stem}-{os.getpid()}.tmp"
    )
    try:
        temporary_path.write_text(
            json.dumps({"level": round(max(0.0, level), 2)}),
            encoding="utf-8",
        )
        os.replace(temporary_path, VOICE_METER_PATH)
    except OSError as error:
        logging.debug("Měřič mikrofonu nelze právě obnovit: %s", error)
        try:
            temporary_path.unlink(missing_ok=True)
        except OSError:
            pass


def run_curl(arguments: list[str]) -> dict[str, object]:
    """Volá pouze místní API přes curl a vrací JSON odpověď."""
    result = subprocess.run(
        ["curl.exe", "--fail", "--silent", "--show-error", *arguments],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    return json.loads(result.stdout)


def transcribe(audio_path: Path, language: str) -> dict[str, object]:
    """Přepíše WAV soubor pomocí lokálního Faster-Whisper serveru."""
    arguments = [
        "-X", "POST", f"{API_URL}/v1/speech/transcribe",
        "-F", f"file=@{audio_path}",
    ]
    if language.lower() in {"cs", "en"}:
        arguments.extend(["-F", f"language={language.lower()}"])
    result = run_curl(arguments)
    return {
        "text": str(result.get("text", "")).strip(),
        "language": str(result.get("language") or "auto"),
        "confidence": float(result.get("confidence") or 0.0),
    }


def is_stop_command(command: str) -> bool:
    """Rozpozná jednoznačné české povely pro okamžité umlčení."""
    normalized = " ".join(command.lower().strip().split())
    return normalized in {
        "stop", "ticho", "jarvis stop", "hej jarvis stop",
        "jarvis ticho", "hej jarvis ticho",
    }


def sentence_chunks(buffer: str, final: bool = False) -> tuple[list[str], str]:
    """Oddělí hotové věty tak, aby Piper mohl začít mluvit před koncem odpovědi."""
    sentences: list[str] = []
    remaining = buffer.strip()
    while True:
        boundary = next((index for index, char in enumerate(remaining) if char in ".?!"), -1)
        if boundary < 0:
            break
        sentence = remaining[:boundary + 1].strip()
        if sentence:
            sentences.append(sentence)
        remaining = remaining[boundary + 1:].strip()
    if final and remaining:
        sentences.append(remaining)
        remaining = ""
    return sentences, remaining


def resolve_input_device(preferred_device: object) -> int | None:
    """Vybere konkrétní mikrofon podle názvu, jinak ponechá výchozí Windows vstup."""
    preferred = str(preferred_device or "").strip().casefold()
    devices: list[dict[str, Any]] = list(sd.query_devices())
    if preferred:
        for index, device in enumerate(devices):
            if int(device.get("max_input_channels", 0)) > 0 and preferred in str(device["name"]).casefold():
                logging.info("Používám mikrofon %s: %s", index, device["name"])
                return index
        logging.warning("Požadovaný mikrofon '%s' nebyl nalezen, používám výchozí Windows vstup.", preferred_device)
    default_device = sd.default.device[0]
    return int(default_device) if isinstance(default_device, (int, np.integer)) and default_device >= 0 else None


class AudioInput:
    """Mikrofonní fronta, kterou lze bezpečně kontrolovat i během generování odpovědi."""

    def __init__(self, sample_rate: int, device: int | None) -> None:
        self.blocks: queue.Queue[bytes] = queue.Queue(maxsize=360)
        self.level = 0.0
        self.noise_floor = 250.0
        self.stream = sd.RawInputStream(
            device=device,
            samplerate=sample_rate,
            blocksize=1280,
            channels=1,
            dtype="int16",
            callback=self._on_audio,
        )

    def _on_audio(self, indata: np.ndarray, _frames: int, _time: object, _status: object) -> None:
        block = bytes(indata)
        samples = np.frombuffer(block, dtype=np.int16)
        if samples.size:
            self.level = float(np.sqrt(np.mean(samples.astype(np.float32) ** 2)))
            if self.level <= max(self.noise_floor * 1.35, 450.0):
                self.noise_floor = self.noise_floor * 0.92 + self.level * 0.08
        try:
            self.blocks.put_nowait(block)
        except queue.Full:
            try:
                self.blocks.get_nowait()
            except queue.Empty:
                pass
            self.blocks.put_nowait(block)

    def __enter__(self) -> "AudioInput":
        self.stream.start()
        return self

    def __exit__(self, _type: object, _value: object, _traceback: object) -> None:
        self.stream.stop()
        self.stream.close()

    def read(self, timeout: float | None = None) -> bytes:
        return self.blocks.get(timeout=timeout)

    def read_nowait(self) -> bytes | None:
        try:
            return self.blocks.get_nowait()
        except queue.Empty:
            return None

    def discard_pending(self) -> None:
        """Odstraní staré bloky, aby nový příkaz začal přesně po aktivaci."""
        while True:
            try:
                self.blocks.get_nowait()
            except queue.Empty:
                return


def save_session_answer(messages: list[dict[str, object]], answer: str) -> None:
    """Uloží hotovou odpověď do lokálního kontextu pro další hlasový příkaz."""
    session = json.loads(SESSION_PATH.read_text(encoding="utf-8"))
    session["messages"] = [*messages, {"role": "assistant", "content": answer}][-30:]
    SESSION_PATH.write_text(json.dumps(session, ensure_ascii=False), encoding="utf-8")


def ask_jarvis_stream(
    command: str,
    model: str,
    microphone: AudioInput,
    wake_model: Model,
    sample_rate: int,
    config: dict[str, object],
    language: str,
    on_sentence: Callable[[str], None],
) -> str:
    """Streamuje lokální odpověď a během čekání přijímá hlasový povel STOP."""
    session = json.loads(SESSION_PATH.read_text(encoding="utf-8"))
    messages = [
        {
            "role": str(item.get("role", "user")),
            "content": str(item.get("content", ""))[:900],
        }
        for item in session.get("messages", [])[-6:]
        if isinstance(item, dict) and str(item.get("content", "")).strip()
    ]
    try:
        rules = json.loads(RULES_PATH.read_text(encoding="utf-8")).get("rules", [])
    except (OSError, json.JSONDecodeError):
        rules = []
    if rules:
        voice_rules = [rule[:260] for rule in rules[:12] if rule.strip()]
        messages.insert(0, {"role": "system", "content": "Pravidla hlasového asistenta:\n" + "\n".join(f"- {rule}" for rule in voice_rules)})
    messages.append({"role": "user", "content": command})
    payload = json.dumps({
        "model": model,
        "messages": messages,
        "stream": True,
    }, ensure_ascii=False)
    process = subprocess.Popen([
        "curl.exe", "--fail", "--silent", "--show-error", "--no-buffer",
        "-X", "POST", f"{API_URL}/v1/chat/completions",
        "-H", "Content-Type: application/json",
        "--data", payload,
    ], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8", bufsize=1)
    lines: queue.Queue[str | None] = queue.Queue()

    def read_stream() -> None:
        if process.stdout is None:
            lines.put(None)
            return
        for line in process.stdout:
            lines.put(line)
        lines.put(None)

    threading.Thread(target=read_stream, daemon=True).start()
    answer = ""
    pending = ""
    interrupt_threshold = max(0.50, float(config["wake_threshold"]))
    stream_closed = False
    while not stream_closed:
        if cancellation_requested():
            process.terminate()
            process.wait(timeout=2)
            return ""
        if manual_listen_requested():
            process.terminate()
            process.wait(timeout=2)
            return ""
        for _ in range(12):
            raw_audio = microphone.read_nowait()
            if raw_audio is None:
                break
            samples = np.frombuffer(raw_audio, dtype=np.int16)
            prediction = wake_model.predict(samples)
            wake_detected = float(prediction.get("hey_jarvis", 0.0)) >= interrupt_threshold
            if not wake_detected:
                continue
            publish("listening", text="NASLOUCHÁM PŘERUŠENÍ")
            interruption = capture_command(microphone, sample_rate, config)
            interrupt_path = RUNTIME_DIR / "interrupt-command.wav"
            sf.write(interrupt_path, interruption, sample_rate, subtype="PCM_16")
            interrupt_text = str(transcribe(interrupt_path, language)["text"])
            if is_stop_command(interrupt_text):
                process.terminate()
                process.wait(timeout=2)
                publish("voice_stop", text="HLASOVÝ ÚKOL ZASTAVEN")
                return ""
        try:
            line = lines.get(timeout=0.08)
        except queue.Empty:
            continue
        if line is None:
            stream_closed = True
            continue
        if not line.startswith("data:"):
            continue
        data = line[5:].strip()
        if data == "[DONE]":
            continue
        try:
            token = json.loads(data).get("choices", [{}])[0].get("delta", {}).get("content", "")
        except (json.JSONDecodeError, IndexError, TypeError):
            continue
        if not token:
            continue
        token = str(token)
        answer += token
        pending += token
        sentences, pending = sentence_chunks(pending)
        for sentence in sentences:
            on_sentence(sentence)
    try:
        return_code = process.wait(timeout=5)
    except subprocess.TimeoutExpired as error:
        process.terminate()
        process.wait(timeout=2)
        raise OSError("Lokální model nedokončil hlasovou odpověď včas.") from error
    if return_code != 0:
        detail = process.stderr.read().strip() if process.stderr is not None else ""
        raise OSError(detail or f"Lokální model skončil s kódem {return_code}.")
    final_sentences, _ = sentence_chunks(pending, final=True)
    for sentence in final_sentences:
        on_sentence(sentence)
    answer = answer.strip() or "Odpověď nebyla vrácena."
    save_session_answer(messages, answer)
    return answer


def capture_command(microphone: AudioInput, sample_rate: int, config: dict[str, object]) -> np.ndarray:
    """Nahrává otázku do ticha místo pevně dané délky záznamu."""
    frames: list[np.ndarray] = []
    block_size = 1280
    silence_blocks = 0
    spoken = False
    minimum_blocks = max(1, int(float(config.get("min_command_seconds", 0.8)) * sample_rate / block_size))
    maximum_blocks = max(1, int(float(config.get("max_command_seconds", config["command_seconds"])) * sample_rate / block_size))
    required_silence = max(1, int(float(config.get("silence_seconds", 1.1)) * sample_rate / block_size))
    configured_threshold = float(config.get("speech_threshold", 650))
    threshold = max(360.0, min(configured_threshold, microphone.noise_floor * 1.45))
    speech_start_blocks = max(1, int(float(config.get("speech_start_seconds", 4.0)) * sample_rate / block_size))
    consecutive_speech = 0
    logging.info("Nahrávání hlasu: šum %.0f, práh %.0f", microphone.noise_floor, threshold)
    for index in range(maximum_blocks):
        try:
            raw_audio = microphone.read(timeout=2)
        except queue.Empty:
            logging.error("Mikrofon během nahrávání neposkytl data.")
            break
        samples = np.frombuffer(raw_audio, dtype=np.int16).copy()
        frames.append(samples)
        level = float(np.sqrt(np.mean(samples.astype(np.float32) ** 2)))
        publish_meter(level)
        if level >= threshold:
            consecutive_speech += 1
            spoken = spoken or consecutive_speech >= 2
            silence_blocks = 0
        else:
            consecutive_speech = 0
            if spoken:
                silence_blocks += 1
        if not spoken and index >= speech_start_blocks:
            break
        if spoken and index >= minimum_blocks and silence_blocks >= required_silence:
            break
    return np.concatenate(frames) if frames else np.array([], dtype=np.int16)


def synthesize_answer(voice: PiperVoice, text: str) -> str:
    """Vytvoří lokální WAV pro přerušitelné přehrání přímo v HUDu."""
    HUD_VOICE_DIR.mkdir(parents=True, exist_ok=True)
    filename = f"jarvis-{datetime.now().strftime('%Y%m%d%H%M%S%f')}.wav"
    audio_path = HUD_VOICE_DIR / filename
    try:
        with wave.open(str(audio_path), "wb") as wav_file:
            voice.synthesize_wav(text[:4000], wav_file)
        if audio_path.stat().st_size <= 44:
            raise wave.Error("Piper nevytvořil žádné audio.")
    except (OSError, wave.Error) as error:
        logging.warning("Lokální syntéza řeči selhala: %s", error)
        audio_path.unlink(missing_ok=True)
        return ""
    return f"/voice/{filename}"


def select_model(command: str, router: dict[str, object]) -> str:
    """Zvolí nejvhodnější místní model bez odeslání textu mimo počítač."""
    import re

    request = command.lower()
    if re.search(str(router["coding_pattern"]), request):
        return str(router["coding_model"])
    if re.search(str(router["complex_pattern"]), request):
        return str(router["complex_model"])
    return str(router["default_model"])


def main() -> None:
    """Zpracovává mikrofon se skutečným lokálním wake-word modelem."""
    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(filename=LOG_PATH, level=logging.INFO, encoding="utf-8")
    config = load_config()
    router = json.loads(ROUTER_PATH.read_text(encoding="utf-8"))
    sample_rate = int(config["sample_rate"])
    threshold = float(config["wake_threshold"])
    command_seconds = int(config["command_seconds"])
    language = str(config["language"])
    wake_model = Model(inference_framework="onnx", wakeword_models=["hey_jarvis"])
    piper_voice = PiperVoice.load(str(PIPER_MODEL_PATH))
    publish("voice_ready", text="LOKÁLNÍ HLASOVÝ REŽIM AKTIVNÍ")
    logging.info("Hlasový klient spuštěn")

    input_device = resolve_input_device(config.get("input_device"))
    input_name = sd.query_devices(input_device)["name"] if input_device is not None else "výchozí Windows mikrofon"
    publish("voice_ready", text="LOKÁLNÍ HLASOVÝ REŽIM AKTIVNÍ", device=input_name)
    logging.info("Hlasový klient spuštěn, mikrofon: %s", input_name)

    with AudioInput(sample_rate, input_device) as microphone:
        last_meter_update = 0.0
        speech_blocks = 0
        awaiting_command_until = 0.0
        while True:
            raw_audio = microphone.read()
            now = time.monotonic()
            if now - last_meter_update >= 0.15:
                publish_meter(microphone.level)
                last_meter_update = now
            if not voice_is_enabled():
                speech_blocks = 0
                continue
            manual_listen = consume_manual_listen()
            samples = np.frombuffer(raw_audio, dtype=np.int16)
            prediction = wake_model.predict(samples)
            score = float(prediction.get("hey_jarvis", 0.0))
            wake_detected = score >= threshold
            level = float(np.sqrt(np.mean(samples.astype(np.float32) ** 2)))
            speech_threshold = max(600.0, microphone.noise_floor * 1.80)
            speech_blocks = speech_blocks + 1 if level >= speech_threshold else 0
            automatic_listen = bool(config.get("always_listening", True)) and speech_blocks >= 2
            if not manual_listen and not wake_detected and not automatic_listen:
                continue
            speech_blocks = 0
            if wake_detected:
                publish("wake_detected", text="HEY JARVIS ROZPOZNÁN", score=round(score, 2))
                logging.info("Wake-word rozpoznán: %.2f", score)
            elif automatic_listen:
                publish("listening", text="ŘEČ ROZPOZNÁNA")
                logging.info("Automaticky zachycena řeč: %.0f", level)
            else:
                publish("listening", text="NASLOUCHÁM RUČNÍMU PŘÍKAZU")
                logging.info("Ruční nahrávání aktivováno reaktorem")
            clear_cancellation()
            publish("listening", text="NASLOUCHÁM OTÁZCE")
            if manual_listen:
                microphone.discard_pending()
            frames = capture_command(microphone, sample_rate, config)
            if not frames.size:
                publish("error", text="MIKROFON NEPOSKYTOVAL ŽÁDNÁ DATA")
                continue

            audio_path = RUNTIME_DIR / "last-command.wav"
            sf.write(audio_path, frames, sample_rate, subtype="PCM_16")
            publish("transcribing", text="PŘEPISUJI HLASOVÝ PŘÍKAZ")
            try:
                recognition = transcribe(audio_path, language)
                command = str(recognition["text"])
                spoken_wake_phrase = contains_wake_phrase(command)
                waiting_for_command = time.monotonic() < awaiting_command_until
                if wake_detected or spoken_wake_phrase:
                    command = strip_wake_phrase(command)
                minimum_confidence = float(config.get("auto_transcription_confidence", 0.55))
                if automatic_listen and not spoken_wake_phrase and not waiting_for_command and float(recognition["confidence"]) < minimum_confidence:
                    logging.info("Automatický přepis odmítnut pro nízkou jistotu: %.2f", recognition["confidence"])
                    publish("listening", text="ČEKÁM NA JASNÝ HLASOVÝ PŘÍKAZ")
                    continue
                if automatic_listen and not (spoken_wake_phrase or waiting_for_command):
                    logging.info("Automatický přepis ignorován bez oslovení JARVISu")
                    continue
                if not command:
                    if wake_detected or spoken_wake_phrase:
                        awaiting_command_until = time.monotonic() + 8.0
                        publish("listening", text="JARVIS AKTIVNÍ - ČEKÁM NA PŘÍKAZ")
                        continue
                    publish("error", text="PŘÍKAZ NEBYL ROZPOZNÁN")
                    continue
                awaiting_command_until = 0.0
                if is_stop_command(command):
                    publish("voice_stop", text="HLASOVÝ ÚKOL ZASTAVEN")
                    continue
                model_name = select_model(command, router)
                publish(
                    "voice_command",
                    text=command,
                    model=model_name,
                    language=recognition["language"],
                    confidence=recognition["confidence"],
                )

                def speak_sentence(sentence: str) -> None:
                    if cancellation_requested():
                        return
                    publish(
                        "voice_sentence",
                        text=sentence,
                        model=model_name,
                        audio_url=synthesize_answer(piper_voice, sentence),
                    )

                gemini_preferred = bool(config.get("gemini_live_preferred", True))
                try:
                    if not gemini_preferred or not gemini_live_available(config):
                        raise GeminiLiveError("Gemini Live čeká na další bezpečný pokus.")
                    publish("voice_mode", text="GEMINI LIVE AKTIVNÍ", provider="gemini_live")
                    answer, audio_url = ask_gemini_live(command, cancellation_requested)
                    record_gemini_live_state(True, config)
                    publish("voice_answer", text=answer, model="gemini_live", audio_url=audio_url)
                except GeminiLiveError as error:
                    if cancellation_requested():
                        publish("voice_stop", text="HLASOVÝ ÚKOL ZASTAVEN")
                        continue
                    record_gemini_live_state(False, config)
                    logging.warning("Gemini Live selhalo, přepínám na lokální hlas: %s", error)
                    publish("voice_mode", text="LOKÁLNÍ HLASOVÝ REŽIM - ZÁLOHA", provider="local")
                    answer = ask_jarvis_stream(
                        command,
                        model_name,
                        microphone,
                        wake_model,
                        sample_rate,
                        config,
                        language,
                        speak_sentence,
                    )
                if not answer:
                    publish("voice_stop", text="HLASOVÝ ÚKOL ZASTAVEN")
                    continue
                publish("voice_complete", text=answer, model=model_name)
            except (OSError, subprocess.CalledProcessError, json.JSONDecodeError) as error:
                logging.exception("Hlasové zpracování selhalo")
                publish("error", text=f"HLASOVÝ MODUL: {error}")


if __name__ == "__main__":
    while True:
        try:
            main()
        except Exception as error:
            logging.exception("Hlasový klient se neočekávaně ukončil")
            try:
                publish("error", text=f"HLASOVÝ KLIENT SE OBNOVUJE: {error}")
            except OSError:
                pass
            time.sleep(3)
