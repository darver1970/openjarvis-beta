"""Lokální wake-word klient pro J.A.R.V.I.S.; všechny soubory zůstávají na A:."""

from __future__ import annotations

import json
import logging
import os
import subprocess
from datetime import datetime
from pathlib import Path

import numpy as np
import sounddevice as sd
import soundfile as sf
from openwakeword.model import Model


ROOT = Path(__file__).resolve().parent
CONFIG_PATH = ROOT / "runtime" / "voice-config.json"
ROUTER_PATH = ROOT / "runtime" / "model-router.json"
SESSION_PATH = ROOT / "runtime" / "shared-session.json"
RULES_PATH = ROOT / "runtime" / "jarvis-rules.json"
RUNTIME_DIR = ROOT / "runtime" / "voice"
HUD_EVENT_PATH = ROOT / "hud" / "voice-event.json"
LOG_PATH = ROOT / "runtime" / "voice-client.log"
API_URL = "http://127.0.0.1:8000"


def load_config() -> dict[str, object]:
    """Načte lokální konfiguraci klienta."""
    return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))


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
    result = run_curl([
        "-X", "POST", f"{API_URL}/v1/chat/completions",
        "-H", "Content-Type: application/json",
        "--data", payload,
    ])
    choices = result.get("choices", [])
    answer = "Odpověď nebyla vrácena."
    if choices:
        answer = str(choices[0].get("message", {}).get("content", answer))
    session["messages"] = [*messages, {"role": "assistant", "content": answer}][-30:]
    SESSION_PATH.write_text(json.dumps(session, ensure_ascii=False), encoding="utf-8")
    return answer


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
            prediction = wake_model.predict(np.frombuffer(raw_audio, dtype=np.int16))
            score = float(prediction.get("hey_jarvis", 0.0))
            if score < threshold:
                continue

            publish("wake_detected", text="HEY JARVIS ROZPOZNÁN", score=round(score, 2))
            logging.info("Wake-word rozpoznán: %.2f", score)
            frames = []
            blocks = int(sample_rate * command_seconds / 1280)
            for _ in range(blocks):
                command_audio, _ = stream.read(1280)
                frames.append(np.frombuffer(command_audio, dtype=np.int16).copy())

            audio_path = RUNTIME_DIR / "last-command.wav"
            sf.write(audio_path, np.concatenate(frames), sample_rate, subtype="PCM_16")
            publish("transcribing", text="PŘEPISUJI HLASOVÝ PŘÍKAZ")
            try:
                command = transcribe(audio_path, language)
                if not command:
                    publish("error", text="PŘÍKAZ NEBYL ROZPOZNÁN")
                    continue
                model_name = select_model(command, router)
                publish("voice_command", text=command, model=model_name)
                answer = ask_jarvis(command, model_name)
                publish("voice_answer", text=answer, model=model_name)
            except (OSError, subprocess.CalledProcessError, json.JSONDecodeError) as error:
                logging.exception("Hlasové zpracování selhalo")
                publish("error", text=f"HLASOVÝ MODUL: {error}")


if __name__ == "__main__":
    main()
