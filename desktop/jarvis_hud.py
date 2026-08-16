"""Nativní, izolované okno pro lokální HUD JARVISu."""

from __future__ import annotations

import json
import logging
import os
import sys
from pathlib import Path


def application_root() -> Path:
    """Vrátí kořen instalace pro zdrojový i zabalený program."""
    executable_dir = Path(sys.executable).resolve().parent
    return executable_dir.parent if executable_dir.name.lower() == "desktop" else executable_dir


ROOT = application_root()
RUNTIME = ROOT / "runtime"
WEBVIEW_PROFILE = RUNTIME / "jarvis-webview"
WEBVIEW_PROFILE.mkdir(parents=True, exist_ok=True)
os.environ["WEBVIEW2_USER_DATA_FOLDER"] = str(WEBVIEW_PROFILE)

import webview  # noqa: E402


LOG_PATH = RUNTIME / "jarvis-hud.log"
logging.basicConfig(
    filename=LOG_PATH,
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    encoding="utf-8",
)


def load_borderless_setting() -> bool:
    """Načte volbu bezrámečkového okna, při chybě zachová výchozí hodnotu."""
    settings_path = RUNTIME / "jarvis-settings.json"
    try:
        settings = json.loads(settings_path.read_text(encoding="utf-8"))
        return bool(settings.get("borderless_window", True))
    except (OSError, json.JSONDecodeError) as error:
        logging.warning("Nastavení okna nelze načíst: %s", error)
        return True


class JarvisApi:
    """Rozhraní dostupné HUDu pouze ve vlastním nativním okně."""

    def close(self) -> bool:
        """Ukončí nativní okno po stisku tlačítka UKONČIT v HUDu."""
        if webview.windows:
            webview.windows[0].destroy()
        return True


def main() -> None:
    """Spustí izolovaný lokální host bez externího prohlížeče a rozšíření."""
    hud_url = "http://127.0.0.1:5173/?hud_version=v0.22"
    borderless = load_borderless_setting()
    logging.info("Spouštím nativní HUD: %s", hud_url)
    window = webview.create_window(
        "JARVIS",
        hud_url,
        width=1440,
        height=920,
        min_size=(980, 680),
        frameless=borderless,
        fullscreen=borderless,
        js_api=JarvisApi(),
    )
    webview.start(gui="edgechromium", private_mode=True, debug=False)
    logging.info("Nativní HUD byl ukončen.")


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        logging.exception("Nativní HUD nelze spustit")
        raise SystemExit(f"JARVIS HUD nelze spustit. Podrobnosti: {LOG_PATH}") from error
