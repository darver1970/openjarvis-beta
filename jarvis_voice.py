"""Lokální wake-word klient pro J.A.R.V.I.S.; všechny soubory zůstávají na A:."""

from __future__ import annotations

import json
import logging
import os
import subprocess
import time
import wave
from datetime import datetime
from pathlib import Path

import numpy as np
import sounddevice as sd
import soundfile as sf
from openwakeword.model import Model
from piper import PiperVoice


ROOT = Path(__file__).resolve().parent
CONFIG_PATH = ROOT / "runtime" / "voice-config.json"
ROUTER_PATH = ROOT / "runtime" / "model-router.json"
SESSION_PATH = ROOT / "runtime" / "shared-session.json"
RULES_PATH = ROOT / "runtime" / "jarvis-rules.json"
VOICE_CONTROL_PATH = ROOT / "runtime" / "voice-control.json"
RUNTIME_DIR = ROOT / "runtime" / "voice"
HUD_EVENT_PATH = ROOT / "hud" / "voice-event.json"
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
    state = {"enabled": voice_is_enabled(), "cancel_requested": False}
    VOICE_CONTROL_PATH.write_text(json.dumps(state), encoding="utf-8")


def publish(event_type: str, **payload: object) -> None:
    """Atomicky předá stav lokálnímu HUD bez síťové služby třetí strany."""
    event = {"id": datetime.now().isoformat(), "type": event_type, **payload}
    temporary_path = HUD_EVENT_PATH.with_suffix(".tmp")
    temporary_path.write_text(json.dumps(event, ensure_ascii=False), encoding="utf-8")
    os.replace(temporary_path, HUD_EVENT_PATH)


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


def transcribe(audio_path: Path, language: str) -> str:
    """Přepíše WAV soubor pomocí lokálního Faster-Whisper serveru."""
    result = run_curl([
        "-X", "POST", f"{API_URL}/v1/speech/transcribe",
        "-F", f"file=@{audio_path}",
        "-F", f"language={language}",
    ])
    return str(result.get("text", "")).strip()


def ask_jarvis(command: str, model: str) -> str:
    """Odešle hlasový příkaz výhradně místnímu Jarvis API."""
    session = json.loads(SESSION_PATH.read_text(encoding="utf-8"))
    messages = session.get("messages", [])[-30:]
    try:
        rules = json.loads(RULES_PATH.read_text(encoding="utf-8")).get("rules", [])
    except (OSError, json.JSONDecodeError):
        rules = []
    if rules:
        messages.insert(0, {"role": "system", "content": "Trvalá uživatelská pravidla (dodržuj v rámci bezpečného a zákonného použití):\n" + "\n".join(f"- {rule}" for rule in rules)})
    messages.append({"role": "user", "content": command})
    payload = json.dumps({
        "model": model,
        "messages": messages,
        "stream": False,
    }, ensure_ascii=False)
    process = subprocess.Popen([
        "curl.exe", "--fail", "--silent", "--show-error",
        "-X", "POST", f"{API_URL}/v1/chat/completions",
        "-H", "Content-Type: application/json",
        "--data", payload,
    ], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8")
    while process.poll() is None:
        if cancellation_requested():
            process.terminate()
            process.wait(timeout=2)
            return ""
        time.sleep(0.08)
    if process.returncode != 0:
        raise subprocess.CalledProcessError(process.returncode, process.args)
    result = json.loads(process.stdout.read() if process.stdout else "{}")
    choices = result.get("choices", [])
    answer = "Odpověď nebyla vrácena."
    if choices:
        answer = str(choices[0].get("message", {}).get("content", answer))
    session["messages"] = [*messages, {"role": "assistant", "content": answer}][-30:]
    SESSION_PATH.write_text(json.dumps(session, ensure_ascii=False), encoding="utf-8")
    return answer


def capture_command(stream: sd.RawInputStream, sample_rate: int, config: dict[str, object]) -> np.ndarray:
    """Nahrává otázku do ticha místo pevně dané délky záznamu."""
    frames: list[np.ndarray] = []
    block_size = 1280
    silence_blocks = 0
    spoken = False
    minimum_blocks = max(1, int(float(config.get("min_command_seconds", 0.8)) * sample_rate / block_size))
    maximum_blocks = max(1, int(float(config.get("max_command_seconds", config["command_seconds"])) * sample_rate / block_size))
    required_silence = max(1, int(float(config.get("silence_seconds", 1.1)) * sample_rate / block_size))
    threshold = float(config.get("speech_threshold", 360))
    for index in range(maximum_blocks):
        raw_audio, _ = stream.read(block_size)
        samples = np.frombuffer(raw_audio, dtype=np.int16).copy()
        frames.append(samples)
        level = float(np.sqrt(np.mean(samples.astype(np.float32) ** 2)))
        if level >= threshold:
            spoken = True
            silence_blocks = 0
        elif spoken:
            silence_blocks += 1
        if spoken and index >= minimum_blocks and silence_blocks >= required_silence:
            break
    return np.concatenate(frames)


def synthesize_answer(voice: PiperVoice, text: str) -> str:
    """Vytvoří lokální WAV pro přerušitelné přehrání přímo v HUDu."""
    HUD_VOICE_DIR.mkdir(parents=True, exist_ok=True)
    filename = f"jarvis-{datetime.now().strftime('%Y%m%d%H%M%S%f')}.wav"
    with wave.open(str(HUD_VOICE_DIR / filename), "wb") as wav_file:
        voice.synthesize_wav(text[:4000], wav_file)
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
    publish("voice_ready", text="LOKÁLNÍ HEY JARVIS AKTIVNÍ")
    logging.info("Wake-word klient spuštěn")

    with sd.RawInputStream(
        samplerate=sample_rate,
        blocksize=1280,
        channels=1,
        dtype="int16",
    ) as stream:
        while True:
            raw_audio, _ = stream.read(1280)
            if not voice_is_enabled():
                continue
            prediction = wake_model.predict(np.frombuffer(raw_audio, dtype=np.int16))
            score = float(prediction.get("hey_jarvis", 0.0))
            if score < threshold:
                continue

            publish("wake_detected", text="HEY JARVIS ROZPOZNÁN", score=round(score, 2))
            clear_cancellation()
            logging.info("Wake-word rozpoznán: %.2f", score)
            publish("listening", text="NASLOUCHÁM OTÁZCE")
            frames = capture_command(stream, sample_rate, config)

            audio_path = RUNTIME_DIR / "last-command.wav"
            sf.write(audio_path, np.concatenate(frames), sample_rate, subtype="PCM_16")
            publish("transcribing", text="PŘEPISUJI HLASOVÝ PŘÍKAZ")
            try:
                command = transcribe(audio_path, language)
                if not command:
                    publish("error", text="PŘÍKAZ NEBYL ROZPOZNÁN")
                    continue
                if command.lower().strip() in {"jarvis stop", "hej jarvis stop", "stop"}:
                    publish("voice_stop", text="HLASOVÝ ÚKOL ZASTAVEN")
                    continue
                model_name = select_model(command, router)
                publish("voice_command", text=command, model=model_name)
                answer = ask_jarvis(command, model_name)
                if not answer:
                    publish("voice_stop", text="HLASOVÝ ÚKOL ZASTAVEN")
                    continue
                publish("voice_answer", text=answer, model=model_name, audio_url=synthesize_answer(piper_voice, answer))
            except (OSError, subprocess.CalledProcessError, json.JSONDecodeError) as error:
                logging.exception("Hlasové zpracování selhalo")
                publish("error", text=f"HLASOVÝ MODUL: {error}")


if __name__ == "__main__":
    main()
