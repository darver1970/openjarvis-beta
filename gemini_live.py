"""Bezplatný Gemini Live hlas s lokálním uložením audio odpovědi pro HUD."""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import wave
from datetime import datetime
from pathlib import Path
from typing import Callable
from urllib.parse import quote

import websockets

from jarvis_control import load_cloud_secrets, unprotect_secret


ROOT = Path(__file__).resolve().parent
HUD_VOICE_DIR = ROOT / "hud" / "voice"
MODEL = "gemini-2.5-flash-native-audio-preview-12-2025"


class GeminiLiveError(RuntimeError):
    """Signalizuje stav, kdy má volající přejít na lokální hlas."""


def save_pcm_audio(audio: bytes) -> str:
    """Převede PCM 24 kHz z Gemini na WAV dostupný jen lokálnímu HUDu."""
    if not audio:
        return ""
    HUD_VOICE_DIR.mkdir(parents=True, exist_ok=True)
    target = HUD_VOICE_DIR / f"gemini-{datetime.now().strftime('%Y%m%d%H%M%S%f')}.wav"
    with wave.open(str(target), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(24000)
        output.writeframes(audio)
    return f"/voice/{target.name}"


async def _request_live_answer(command: str, cancelled: Callable[[], bool]) -> tuple[str, bytes]:
    """Pošle text do Live relace a vrátí přepsanou i zvukovou odpověď."""
    encrypted = load_cloud_secrets().get("gemini_free")
    if not encrypted:
        raise GeminiLiveError("Gemini klíč není uložen.")
    try:
        api_key = unprotect_secret(encrypted)
    except Exception as error:
        raise GeminiLiveError(
            "Gemini klíč ze starého Windows nelze odemknout; používám lokální hlas."
        ) from error
    endpoint = (
        "wss://generativelanguage.googleapis.com/ws/"
        "google.ai.generativelanguage.v1beta.GenerativeService.BidiGenerateContent?key="
        f"{quote(api_key)}"
    )
    setup = {
        "setup": {
            "model": f"models/{MODEL}",
            "generationConfig": {"responseModalities": ["AUDIO"]},
            "systemInstruction": {
                "parts": [{
                    "text": (
                        "Jsi hlasový asistent JARVIS. Rozumíš česky i anglicky, "
                        "včetně běžné neformální řeči. Odpovídej ve stejném jazyce, "
                        "ve kterém uživatel mluví, pokud tě výslovně nepožádá jinak."
                    )
                }]
            },
            "inputAudioTranscription": {},
            "outputAudioTranscription": {},
        }
    }
    transcript: list[str] = []
    audio_chunks: list[bytes] = []
    received_output = False
    try:
        async with websockets.connect(endpoint, open_timeout=20, close_timeout=3, max_size=8_000_000) as socket:
            await socket.send(json.dumps(setup))
            setup_reply = json.loads(await asyncio.wait_for(socket.recv(), timeout=20))
            if "setupComplete" not in setup_reply:
                raise GeminiLiveError("Gemini Live nepotvrdilo spuštění relace.")
            await socket.send(json.dumps({"realtimeInput": {"text": command}}))
            while True:
                if cancelled():
                    raise GeminiLiveError("Hlasový úkol byl zastaven.")
                try:
                    reply = json.loads(await asyncio.wait_for(socket.recv(), timeout=3.5 if received_output else 30))
                except TimeoutError:
                    if received_output:
                        break
                    raise GeminiLiveError("Gemini Live nevrátilo odpověď včas.")
                content = reply.get("serverContent", {})
                text = str(content.get("outputTranscription", {}).get("text", ""))
                if text:
                    transcript.append(text)
                    received_output = True
                for part in content.get("modelTurn", {}).get("parts", []):
                    encoded = part.get("inlineData", {}).get("data")
                    if encoded:
                        audio_chunks.append(base64.b64decode(encoded))
                        received_output = True
                if content.get("turnComplete"):
                    break
    except GeminiLiveError:
        raise
    except Exception as error:
        raise GeminiLiveError(f"Gemini Live není dostupné: {str(error)[:220]}") from error
    answer = "".join(transcript).strip()
    if not answer and not audio_chunks:
        raise GeminiLiveError("Gemini Live nevrátilo hlasovou odpověď.")
    return answer or "Gemini Live odpovědělo hlasem.", b"".join(audio_chunks)


def ask_gemini_live(command: str, cancelled: Callable[[], bool]) -> tuple[str, str]:
    """Vrátí přepis a lokální URL audio souboru pro přehrání v HUDu."""
    answer, audio = asyncio.run(_request_live_answer(command, cancelled))
    audio_url = save_pcm_audio(audio)
    logging.info("Gemini Live dokončilo hlasovou odpověď, znaků: %s", len(answer))
    return answer, audio_url
