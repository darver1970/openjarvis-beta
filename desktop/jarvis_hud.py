"""Nativní, izolované okno pro lokální HUD JARVISu."""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any


def application_root() -> Path:
    """Vrátí kořen instalace pro zdrojový i zabalený program."""
    if not getattr(sys, "frozen", False):
        return Path(__file__).resolve().parent.parent
    executable_dir = Path(sys.executable).resolve().parent
    return executable_dir.parent if executable_dir.name.lower() == "desktop" else executable_dir


ROOT = application_root()
RUNTIME = ROOT / "runtime"
WEBVIEW_PROFILE = RUNTIME / "jarvis-webview"
QUARANTINE_DIR = RUNTIME / "quarantine"
SNAPSHOT_DIR = RUNTIME / "snapshots"
WEBVIEW_PROFILE.mkdir(parents=True, exist_ok=True)
QUARANTINE_DIR.mkdir(parents=True, exist_ok=True)
SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
os.environ["WEBVIEW2_USER_DATA_FOLDER"] = str(WEBVIEW_PROFILE)

import webview  # noqa: E402


LOG_PATH = RUNTIME / "jarvis-hud.log"
logging.basicConfig(
    filename=LOG_PATH,
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    encoding="utf-8",
)
BROWSER_WINDOW: Any | None = None
BROWSER_TABS: list[dict[str, str]] = []
ACTIVE_BROWSER_TAB_ID = ""
TEXT_EXTENSIONS = {".css", ".html", ".js", ".json", ".md", ".ps1", ".py", ".txt", ".yml", ".yaml"}
HIDDEN_DIRECTORIES = {".git", ".venv", "__pycache__", "node_modules", "pyinstaller-build", "pyinstaller-spec"}


def project_path(relative_path: str = "") -> Path:
    """Vyřeší cestu a odmítne opuštění kořene projektu."""
    candidate = (ROOT / relative_path).resolve()
    if candidate != ROOT and ROOT not in candidate.parents:
        raise ValueError("Cesta je mimo projekt JARVISu.")
    return candidate


def relative_project_path(path: Path) -> str:
    """Vrátí bezpečnou relativní cestu pro přenos do HUDu."""
    return path.relative_to(ROOT).as_posix() if path != ROOT else ""


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

    def list_files(self, relative_path: str = "") -> dict[str, Any]:
        """Vrátí omezený strom souborů bez soukromých cache a závislostí."""
        path = project_path(relative_path)
        if not path.is_dir():
            raise ValueError("Vybraná cesta není adresář.")
        entries = []
        for item in sorted(path.iterdir(), key=lambda value: (not value.is_dir(), value.name.lower()))[:160]:
            if item.name in HIDDEN_DIRECTORIES or item.name == "runtime":
                continue
            entries.append({
                "name": item.name,
                "path": relative_project_path(item),
                "kind": "directory" if item.is_dir() else "file",
                "size": item.stat().st_size if item.is_file() else 0,
            })
        return {"path": relative_project_path(path), "parent": relative_project_path(path.parent) if path != ROOT else None, "entries": entries}

    def read_file(self, relative_path: str) -> dict[str, str]:
        """Načte pouze krátký textový soubor z projektu pro náhled a kontext."""
        path = project_path(relative_path)
        if not path.is_file() or path.suffix.lower() not in TEXT_EXTENSIONS:
            raise ValueError("Tento soubor nelze v pracovní ploše zobrazit.")
        if path.stat().st_size > 260_000:
            raise ValueError("Soubor je pro náhled příliš velký.")
        return {"path": relative_project_path(path), "content": path.read_text(encoding="utf-8", errors="replace")}

    def git_status(self) -> dict[str, Any]:
        """Vrátí stav Git bez úprav pracovního stromu."""
        result = subprocess.run(["git", "-C", str(ROOT), "status", "--short"], capture_output=True, text=True, encoding="utf-8", timeout=8, check=False)
        return {"available": result.returncode == 0, "entries": result.stdout.splitlines()[:100] if result.returncode == 0 else [], "error": result.stderr.strip()}

    def git_diff(self, relative_path: str) -> dict[str, str]:
        """Vrátí pouze lokální diff bezpečně vybraného projektového souboru."""
        path = project_path(relative_path)
        if not path.is_file():
            raise ValueError("Soubor pro diff neexistuje.")
        relative = relative_project_path(path)
        result = subprocess.run(["git", "-C", str(ROOT), "diff", "--", relative], capture_output=True, text=True, encoding="utf-8", timeout=8, check=False)
        return {"path": relative, "content": result.stdout or "Žádné neuložené změny Git."}

    def create_snapshot(self, label: str = "") -> dict[str, str]:
        """Vytvoří lokální návratový bod pouze z verzovaných textových souborů."""
        clean_label = re.sub(r"[^a-zA-Z0-9_-]+", "-", label).strip("-")[:40] or "bod"
        target = SNAPSHOT_DIR / f"{datetime.now().strftime('%Y%m%d-%H%M%S')}-{clean_label}"
        target.mkdir(parents=True, exist_ok=False)
        tracked = subprocess.run(["git", "-C", str(ROOT), "ls-files"], capture_output=True, text=True, encoding="utf-8", timeout=8, check=False)
        for relative in tracked.stdout.splitlines():
            source = project_path(relative)
            if source.is_file() and source.suffix.lower() in TEXT_EXTENSIONS:
                destination = target / relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, destination)
        logging.info("Vytvořen návratový bod %s", target.name)
        return {"name": target.name, "path": str(target)}

    def list_quarantine(self) -> list[dict[str, Any]]:
        """Vypíše soubory čekající v lokální karanténě."""
        return [{"name": item.name, "size": item.stat().st_size, "created_at": datetime.fromtimestamp(item.stat().st_ctime).isoformat(timespec="seconds")} for item in sorted(QUARANTINE_DIR.iterdir(), key=lambda value: value.stat().st_ctime, reverse=True)[:80] if item.is_file()]

    def _validate_browser_url(self, address: str) -> str:
        """Omezí JARVIS WEB na ručně zadané bezpečné HTTP(S) adresy."""
        url = address.strip() or "https://github.com/"
        if not re.fullmatch(r"https?://[^\s]+", url, flags=re.IGNORECASE):
            raise ValueError("Adresa musí začínat http:// nebo https://.")
        return url

    def _tab_payload(self) -> dict[str, Any]:
        """Vrátí serializovatelný stav spravovaných karet JARVIS WEB."""
        return {"active_tab_id": ACTIVE_BROWSER_TAB_ID, "tabs": BROWSER_TABS}

    def _select_browser_tab(self, tab_id: str) -> dict[str, Any]:
        """Aktivuje existující kartu a načte ji do vlastního nativního okna."""
        global ACTIVE_BROWSER_TAB_ID
        tab = next((item for item in BROWSER_TABS if item["id"] == tab_id), None)
        if tab is None:
            raise ValueError("Vybraná karta JARVIS WEB neexistuje.")
        ACTIVE_BROWSER_TAB_ID = tab_id
        if BROWSER_WINDOW is not None:
            BROWSER_WINDOW.load_url(tab["url"])
            BROWSER_WINDOW.show()
        return self._tab_payload()

    def _open_browser_window(self, url: str) -> None:
        """Otevře nebo přesměruje výhradně vlastní webové okno JARVISu."""
        global BROWSER_WINDOW
        if BROWSER_WINDOW is not None:
            try:
                BROWSER_WINDOW.load_url(url)
                BROWSER_WINDOW.show()
                return
            except Exception as error:
                logging.warning("Původní okno JARVIS WEB nelze použít, vytvářím nové: %s", error)
                BROWSER_WINDOW = None
        BROWSER_WINDOW = webview.create_window(
            "JARVIS WEB",
            url,
            width=620,
            height=900,
            min_size=(420, 520),
            x=820,
            y=54,
            resizable=True,
            frameless=False,
        )

    def list_browser_tabs(self) -> dict[str, Any]:
        """Vrátí karty spravované Pracovnou bez přístupu k obsahu webu."""
        return self._tab_payload()

    def open_browser(self, address: str = "https://github.com/") -> dict[str, Any]:
        """Přesměruje aktivní kartu na ručně zadaný HTTP(S) cíl."""
        global ACTIVE_BROWSER_TAB_ID
        url = self._validate_browser_url(address)
        if not BROWSER_TABS:
            return self.new_browser_tab(url)
        tab = next((item for item in BROWSER_TABS if item["id"] == ACTIVE_BROWSER_TAB_ID), BROWSER_TABS[0])
        tab["url"] = url
        ACTIVE_BROWSER_TAB_ID = tab["id"]
        self._open_browser_window(url)
        logging.info("Uživatel otevřel JARVIS WEB: %s", url)
        return {"url": url, **self._tab_payload()}

    def new_browser_tab(self, address: str = "https://github.com/") -> dict[str, Any]:
        """Vytvoří novou kartu prohlížeče v pracovní ploše JARVISu."""
        global ACTIVE_BROWSER_TAB_ID
        url = self._validate_browser_url(address)
        tab_id = f"tab-{datetime.now().strftime('%Y%m%d%H%M%S%f')}"
        BROWSER_TABS.append({"id": tab_id, "url": url})
        ACTIVE_BROWSER_TAB_ID = tab_id
        self._open_browser_window(url)
        logging.info("Uživatel otevřel novou kartu JARVIS WEB: %s", url)
        return {"url": url, **self._tab_payload()}

    def select_browser_tab(self, tab_id: str) -> dict[str, Any]:
        """Přepne kartu JARVIS WEB z Pracovny."""
        return self._select_browser_tab(tab_id)

    def close_browser_tab(self, tab_id: str) -> dict[str, Any]:
        """Zavře kartu v Pracovně; vždy ponechá jednu bezpečnou výchozí kartu."""
        global ACTIVE_BROWSER_TAB_ID
        if len(BROWSER_TABS) <= 1:
            BROWSER_TABS.clear()
            ACTIVE_BROWSER_TAB_ID = ""
            return self.new_browser_tab("https://github.com/")
        index = next((position for position, item in enumerate(BROWSER_TABS) if item["id"] == tab_id), -1)
        if index < 0:
            raise ValueError("Vybraná karta JARVIS WEB neexistuje.")
        BROWSER_TABS.pop(index)
        next_tab = BROWSER_TABS[max(0, index - 1)]
        return self._select_browser_tab(next_tab["id"])

    def download_to_quarantine(self, address: str) -> dict[str, Any]:
        """Stáhne ručně určený soubor do karantény s limitem velikosti a času."""
        url = self._validate_browser_url(address)
        name = url.split("?", maxsplit=1)[0].rsplit("/", maxsplit=1)[-1]
        safe_name = re.sub(r"[^a-zA-Z0-9._-]", "_", name)[:100] or "download.bin"
        target = QUARANTINE_DIR / f"{datetime.now().strftime('%Y%m%d-%H%M%S')}-{safe_name}"
        result = subprocess.run(
            ["curl.exe", "--fail", "--location", "--silent", "--show-error", "--max-time", "180", "--max-filesize", "104857600", "--output", str(target), url],
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=190,
            check=False,
        )
        if result.returncode != 0 or not target.is_file():
            target.unlink(missing_ok=True)
            raise ValueError(f"Stažení do karantény selhalo: {result.stderr.strip() or 'neznámá chyba'}")
        logging.info("Uživatel stáhl soubor do karantény: %s", target.name)
        return {"name": target.name, "size": target.stat().st_size}

    def browser_action(self, action: str) -> bool:
        """Provede pouze lokální navigační akci ve vlastním webovém okně."""
        if BROWSER_WINDOW is None:
            raise ValueError("JARVIS WEB není otevřený.")
        scripts = {
            "back": "history.back()",
            "forward": "history.forward()",
            "reload": "location.reload()",
        }
        if action not in scripts:
            raise ValueError("Nepovolená akce prohlížeče.")
        BROWSER_WINDOW.evaluate_js(scripts[action])
        logging.info("JARVIS WEB navigace: %s", action)
        return True


def main() -> None:
    """Spustí izolovaný lokální host bez externího prohlížeče a rozšíření."""
    hud_url = "http://127.0.0.1:5173/?hud_version=v0.5"
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
        easy_drag=False,
        js_api=JarvisApi(),
    )
    # Přihlášení zůstává pouze v izolovaném profilu JARVISu, ne v prohlížeči uživatele.
    webview.start(gui="edgechromium", private_mode=False, debug=False)
    logging.info("Nativní HUD byl ukončen.")


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        logging.exception("Nativní HUD nelze spustit")
        raise SystemExit(f"JARVIS HUD nelze spustit. Podrobnosti: {LOG_PATH}") from error
