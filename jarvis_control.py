"""Lokální API pro trvalá pravidla JARVISu uložená výhradně na disku A:."""

import json
import logging
import os
import subprocess
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent
RULES_PATH = ROOT / "runtime" / "jarvis-rules.json"
SETTINGS_PATH = ROOT / "runtime" / "jarvis-settings.json"
PROJECTS_PATH = ROOT / "runtime" / "jarvis-projects.json"
LOG_PATH = ROOT / "runtime" / "jarvis-control.log"
STARTUP_VALUE_NAME = "OpenJarvisBeta"
STARTUP_REGISTRY_PATH = r"HKCU:\Software\Microsoft\Windows\CurrentVersion\Run"
logging.basicConfig(filename=LOG_PATH, level=logging.INFO, encoding="utf-8")


def load_rules() -> list[str]:
    try:
        payload = json.loads(RULES_PATH.read_text(encoding="utf-8"))
        return [str(rule) for rule in payload.get("rules", []) if str(rule).strip()]
    except (OSError, json.JSONDecodeError):
        return []


def save_rules(rules: list[str]) -> None:
    RULES_PATH.parent.mkdir(parents=True, exist_ok=True)
    temporary = RULES_PATH.with_suffix(".tmp")
    temporary.write_text(json.dumps({"rules": rules[-80:]}, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(RULES_PATH)


def load_document(path: Path, key: str) -> dict[str, Any]:
    """Načte lokální konfigurační dokument s bezpečným výchozím obsahem."""
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {key: []}
    except (OSError, json.JSONDecodeError):
        return {key: []}


def save_document(path: Path, payload: dict[str, Any]) -> None:
    """Zapíše dokument atomicky výhradně do runtime adresáře na A:."""
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def run_startup_command(
    command: str,
    environment: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    """Spustí pevně definovaný příkaz pro správu autostartu ve Windows."""
    if os.name != "nt":
        raise ValueError("Automatické spuštění je podporováno pouze ve Windows.")
    return subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", command],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=10,
        check=False,
        env=environment,
    )


def startup_is_enabled() -> bool:
    """Ověří existenci vlastní položky JARVISu v registru aktuálního uživatele."""
    if os.name != "nt":
        return False
    command = (
        f"$item = Get-ItemProperty -Path '{STARTUP_REGISTRY_PATH}' "
        f"-Name '{STARTUP_VALUE_NAME}' -ErrorAction SilentlyContinue; "
        "if ($null -ne $item) { exit 0 }; exit 1"
    )
    return run_startup_command(command).returncode == 0


def set_startup_enabled(enabled: bool) -> bool:
    """Přidá nebo odstraní jedinou bezpečně definovanou položku autostartu."""
    startup_script = ROOT / "spustit-jarvis.ps1"
    if not startup_script.is_file():
        raise ValueError("Spouštěcí skript JARVISu nebyl nalezen.")

    if enabled:
        environment = os.environ.copy()
        environment["JARVIS_STARTUP_SCRIPT"] = str(startup_script)
        command = (
            "$path = [System.IO.Path]::GetFullPath($env:JARVIS_STARTUP_SCRIPT); "
            "$value = 'powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File ' + "
            "('\\\"' + $path + '\\\"'); "
            f"New-Item -Path '{STARTUP_REGISTRY_PATH}' -Force | Out-Null; "
            f"New-ItemProperty -Path '{STARTUP_REGISTRY_PATH}' -Name '{STARTUP_VALUE_NAME}' "
            "-Value $value -PropertyType String -Force | Out-Null"
        )
    else:
        environment = None
        command = (
            f"Remove-ItemProperty -Path '{STARTUP_REGISTRY_PATH}' -Name '{STARTUP_VALUE_NAME}' "
            "-ErrorAction SilentlyContinue"
        )

    result = run_startup_command(command, environment)
    if result.returncode != 0:
        raise ValueError((result.stderr or "Nastavení automatického spuštění selhalo.").strip())
    logging.info("Automatické spuštění po přihlášení: %s", enabled)
    return startup_is_enabled()


class Handler(BaseHTTPRequestHandler):
    """Povoluje pouze lokální čtení a bezpečnou správu textových pravidel."""

    def send_json(self, payload: dict[str, Any], status: int = 200) -> None:
        raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "http://127.0.0.1:5173")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_OPTIONS(self) -> None:
        """Povolí pouze lokální prohlížeč HUD pro ukládání konfigurace."""
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "http://127.0.0.1:5173")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_GET(self) -> None:
        if self.path == "/rules":
            self.send_json({"rules": load_rules()})
        elif self.path == "/settings":
            settings = load_document(SETTINGS_PATH, "settings")
            settings["start_with_windows"] = startup_is_enabled()
            self.send_json(settings)
        elif self.path == "/projects":
            self.send_json(load_document(PROJECTS_PATH, "projects"))
        else:
            self.send_json({"error": "Nenalezeno"}, 404)

    def do_POST(self) -> None:
        try:
            length = int(self.headers.get("Content-Length", "0"))
            data = json.loads(self.rfile.read(min(length, 12000)).decode("utf-8"))
            if self.path == "/rules/remove":
                index = int(data.get("index", -1))
                rules = load_rules()
                if index < 0 or index >= len(rules):
                    raise ValueError("Pravidlo nebylo nalezeno.")
                removed = rules.pop(index)
                save_rules(rules)
                logging.info("Odstraněno pravidlo: %s", removed[:120])
                self.send_json({"rules": rules, "removed": removed})
                return
            if self.path == "/processes/terminate":
                pid = int(data.get("pid", 0))
                if pid <= 4 or pid == os.getpid():
                    raise ValueError("Tento systémový proces nelze ukončit.")
                command = (
                    f"$process = Get-Process -Id {pid} -ErrorAction Stop; "
                    "$protected = 'System','Registry','smss','csrss','wininit','winlogon','services','lsass'; "
                    "if ($protected -contains $process.ProcessName) { throw 'Chráněný proces Windows.' }; "
                    "Stop-Process -Id $process.Id -ErrorAction Stop"
                )
                result = subprocess.run(
                    ["powershell", "-NoProfile", "-Command", command],
                    capture_output=True, text=True, encoding="utf-8", timeout=10,
                )
                if result.returncode != 0:
                    raise ValueError((result.stderr or "Proces nelze ukončit.").strip())
                logging.info("Ukončen proces PID %s na výslovnou žádost uživatele", pid)
                self.send_json({"terminated": pid})
                return
            if self.path == "/settings":
                allowed = {
                    "default_model", "voice_output", "wake_word", "internet_mode",
                    "project_start_required", "start_with_windows", "borderless_window",
                }
                current = load_document(SETTINGS_PATH, "settings")
                for key, value in data.items():
                    if key in allowed:
                        current[key] = value
                if "start_with_windows" in data:
                    if not isinstance(data["start_with_windows"], bool):
                        raise ValueError("Automatické spuštění musí mít hodnotu ano nebo ne.")
                    current["start_with_windows"] = set_startup_enabled(data["start_with_windows"])
                save_document(SETTINGS_PATH, current)
                self.send_json(current)
                return
            if self.path == "/projects":
                name = str(data.get("name", "")).strip()
                if not name or len(name) > 120:
                    raise ValueError("Název projektu musí mít 1 až 120 znaků.")
                current = load_document(PROJECTS_PATH, "projects")
                projects = current.get("projects", [])
                projects.append({"name": name, "created_at": datetime.now().isoformat(timespec="seconds")})
                current["projects"] = projects[-50:]
                save_document(PROJECTS_PATH, current)
                self.send_json(current)
                return
            if self.path != "/rules":
                self.send_json({"error": "Nenalezeno"}, 404)
                return
            rule = str(data.get("rule", "")).strip()
            if not rule or len(rule) > 1000:
                raise ValueError("Pravidlo musí mít 1 až 1000 znaků.")
            rules = load_rules()
            if rule not in rules:
                rules.append(rule)
                save_rules(rules)
            logging.info("Uloženo pravidlo: %s", rule[:120])
            self.send_json({"rules": rules, "saved": rule})
        except (ValueError, json.JSONDecodeError) as error:
            self.send_json({"error": str(error)}, 400)

    def log_message(self, format_text: str, *args: Any) -> None:
        logging.info(format_text, *args)


if __name__ == "__main__":
    ThreadingHTTPServer(("127.0.0.1", 8123), Handler).serve_forever()
