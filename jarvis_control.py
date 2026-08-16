"""Lokální API pro trvalá pravidla JARVISu uložená výhradně na disku A:."""

import json
import logging
import os
import re
import subprocess
import threading
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent
CONTROL_PORT = int(os.environ.get("JARVIS_CONTROL_PORT", "8126"))
RULES_PATH = ROOT / "runtime" / "jarvis-rules.json"
SETTINGS_PATH = ROOT / "runtime" / "jarvis-settings.json"
VOICE_CONTROL_PATH = ROOT / "runtime" / "voice-control.json"
PROJECTS_PATH = ROOT / "runtime" / "jarvis-projects.json"
AGENTS_PATH = ROOT / "runtime" / "jarvis-agents.json"
DEFAULT_AGENTS_PATH = ROOT / "defaults" / "jarvis-agents.json"
AGENT_CATALOG_PATH = ROOT / "defaults" / "jarvis-agent-catalog.json"
PROJECT_MEMORY_PATH = ROOT / "runtime" / "jarvis-project-memory.json"
LOG_PATH = ROOT / "runtime" / "jarvis-control.log"
PROJECT_MEMORY_LOCK = threading.Lock()
STARTUP_VALUE_NAME = "OpenJarvisBeta"
STARTUP_REGISTRY_PATH = r"HKCU:\Software\Microsoft\Windows\CurrentVersion\Run"
LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
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


def load_project_memory() -> dict[str, Any]:
    """Načte sdílené poznatky o projektu se stabilní strukturou."""
    memory = load_document(PROJECT_MEMORY_PATH, "entries")
    memory.setdefault("project", "OpenJarvis Beta")
    memory.setdefault("summary", "Lokální JARVIS pro Windows.")
    entries = memory.get("entries", [])
    memory["entries"] = [entry for entry in entries if isinstance(entry, dict)][-120:]
    return memory


def append_project_memory(data: dict[str, Any]) -> dict[str, Any]:
    """Přidá krátký ověřený poznatek bez možnosti zápisu souborových cest či příkazů."""
    entry_type = str(data.get("type", "insight")).strip().lower()
    title = str(data.get("title", "")).strip()
    summary = str(data.get("summary", "")).strip()
    if entry_type not in {"decision", "insight", "test", "user_preference", "issue"}:
        raise ValueError("Typ projektového záznamu není povolen.")
    if not 3 <= len(title) <= 120 or not 5 <= len(summary) <= 900:
        raise ValueError("Název musí mít 3 až 120 a shrnutí 5 až 900 znaků.")
    if any(token in summary.lower() for token in ("powershell -", "rm -rf", "git push", "curl |")):
        raise ValueError("Projektová paměť nesmí obsahovat spustitelné příkazy.")
    with PROJECT_MEMORY_LOCK:
        memory = load_project_memory()
        memory["entries"].append({
            "type": entry_type,
            "title": title,
            "summary": summary,
            "source": "jarvis" if data.get("source") == "jarvis" else "user",
            "created_at": datetime.now().isoformat(timespec="seconds"),
        })
        memory["entries"] = memory["entries"][-120:]
        save_document(PROJECT_MEMORY_PATH, memory)
    logging.info("Uložen projektový poznatek: %s", title[:120])
    return memory


def load_agents() -> dict[str, Any]:
    """Načte agenty a při prvním běhu založí výchozí lokální registr."""
    if not AGENTS_PATH.exists() and DEFAULT_AGENTS_PATH.exists():
        payload = load_document(DEFAULT_AGENTS_PATH, "agents")
        save_document(AGENTS_PATH, payload)
    current = load_document(AGENTS_PATH, "agents")
    agents = current.get("agents", [])
    if not isinstance(agents, list):
        agents = []
    current["agents"] = [agent for agent in agents if isinstance(agent, dict)]
    current.setdefault("active_agent_id", current["agents"][0].get("id", "jarvis") if current["agents"] else "")
    return current


def load_agent_catalog() -> list[dict[str, Any]]:
    """Vrátí pouze lokálně uložený katalog bez síťového vyhledávání."""
    catalog = load_document(AGENT_CATALOG_PATH, "agents").get("agents", [])
    return [agent for agent in catalog if isinstance(agent, dict)]


def agent_by_id(agents: list[dict[str, Any]], agent_id: str) -> dict[str, Any]:
    """Vyhledá agenta podle stabilního identifikátoru."""
    for agent in agents:
        if agent.get("id") == agent_id:
            return agent
    raise ValueError("Agent nebyl nalezen.")


def normalize_agent_id(value: Any) -> str:
    """Přijímá pouze krátké identifikátory bez cesty nebo příkazů."""
    agent_id = str(value or "").strip().lower()
    if not re.fullmatch(r"[a-z0-9-]{2,48}", agent_id):
        raise ValueError("Identifikátor agenta obsahuje nepovolené znaky.")
    return agent_id


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
            f"if (Test-Path -LiteralPath '{STARTUP_REGISTRY_PATH}') {{ "
            f"Remove-ItemProperty -Path '{STARTUP_REGISTRY_PATH}' -Name '{STARTUP_VALUE_NAME}' "
            "-ErrorAction SilentlyContinue }; exit 0"
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
        elif self.path == "/agents":
            self.send_json(load_agents())
        elif self.path == "/agents/catalog":
            self.send_json({"agents": load_agent_catalog()})
        elif self.path == "/project-memory":
            self.send_json(load_project_memory())
        elif self.path == "/voice/state":
            state = load_document(VOICE_CONTROL_PATH, "voice")
            state.setdefault("enabled", True)
            state.setdefault("cancel_requested", False)
            self.send_json(state)
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
            if self.path == "/project-memory":
                self.send_json(append_project_memory(data))
                return
            if self.path == "/voice/state":
                enabled = data.get("enabled")
                if not isinstance(enabled, bool):
                    raise ValueError("Stav hlasového modulu musí být ano nebo ne.")
                state = load_document(VOICE_CONTROL_PATH, "voice")
                state["enabled"] = enabled
                state["cancel_requested"] = not enabled
                save_document(VOICE_CONTROL_PATH, state)
                self.send_json(state)
                return
            if self.path == "/voice/stop":
                state = load_document(VOICE_CONTROL_PATH, "voice")
                state.setdefault("enabled", True)
                state["cancel_requested"] = True
                save_document(VOICE_CONTROL_PATH, state)
                self.send_json(state)
                return
            if self.path == "/agents/activate":
                agent_id = normalize_agent_id(data.get("agent_id"))
                current = load_agents()
                agent_by_id(current["agents"], agent_id)
                current["active_agent_id"] = agent_id
                save_document(AGENTS_PATH, current)
                logging.info("Aktivní agent: %s", agent_id)
                self.send_json(current)
                return
            if self.path == "/agents/toggle":
                agent_id = normalize_agent_id(data.get("agent_id"))
                current = load_agents()
                agent = agent_by_id(current["agents"], agent_id)
                if agent.get("id") == "jarvis":
                    raise ValueError("Hlavního agenta JARVIS nelze pozastavit.")
                agent["status"] = "paused" if agent.get("status") == "ready" else "ready"
                save_document(AGENTS_PATH, current)
                logging.info("Stav agenta %s: %s", agent_id, agent["status"])
                self.send_json(current)
                return
            if self.path == "/agents/install":
                catalog_id = normalize_agent_id(data.get("catalog_id"))
                confirmed = data.get("confirmed") is True
                catalog_agent = agent_by_id(load_agent_catalog(), catalog_id)
                if catalog_agent.get("price_type") == "paid":
                    self.send_json({
                        "purchase_required": True,
                        "agent": catalog_agent,
                        "message": "Placeného agenta je nutné zakoupit ručně u uvedeného dodavatele.",
                    })
                    return
                if not confirmed:
                    raise ValueError("Instalace bezplatného agenta vyžaduje potvrzení.")
                current = load_agents()
                if any(agent.get("id") == catalog_id for agent in current["agents"]):
                    raise ValueError("Tento agent je již nainstalován.")
                installed = {
                    "id": catalog_id,
                    "name": str(catalog_agent.get("name", "Agent")),
                    "role": str(catalog_agent.get("role", "Pomocný agent")),
                    "model": str(catalog_agent.get("model", "qwen3.5:4b")),
                    "status": "ready",
                    "rules": [str(rule) for rule in catalog_agent.get("rules", [])][:12],
                    "permissions": [str(item) for item in catalog_agent.get("permissions", [])][:12],
                    "installed_at": datetime.now().isoformat(timespec="seconds"),
                }
                current["agents"].append(installed)
                save_document(AGENTS_PATH, current)
                logging.info("Nainstalován bezplatný agent: %s", catalog_id)
                self.send_json(current)
                return
            if self.path == "/agents/tasks":
                task = str(data.get("task", "")).strip()
                agent_ids = data.get("agent_ids", [])
                if not task or len(task) > 2000:
                    raise ValueError("Úkol pro agenty musí mít 1 až 2000 znaků.")
                if not isinstance(agent_ids, list) or not 1 <= len(agent_ids) <= 6:
                    raise ValueError("Vyberte 1 až 6 agentů.")
                current = load_agents()
                selected = [agent_by_id(current["agents"], normalize_agent_id(value)) for value in agent_ids]
                ready = [agent for agent in selected if agent.get("status") == "ready"]
                if not ready:
                    raise ValueError("Žádný vybraný agent není připraven.")
                task_id = f"task-{datetime.now().strftime('%Y%m%d%H%M%S%f')}"
                task_entry = {
                    "id": task_id,
                    "task": task,
                    "agents": [agent["id"] for agent in ready],
                    "status": "running",
                    "created_at": datetime.now().isoformat(timespec="seconds"),
                }
                for agent in ready:
                    agent["status"] = "working"
                    agent["current_task"] = task
                current["tasks"] = (current.get("tasks", []) + [task_entry])[-30:]
                save_document(AGENTS_PATH, current)
                logging.info("Spuštěn souběžný úkol %s pro %s agenty", task_id, len(ready))
                self.send_json({"task_id": task_id, "agents": ready})
                return
            if self.path == "/agents/tasks/complete":
                task_id = str(data.get("task_id", "")).strip()
                agent_id = normalize_agent_id(data.get("agent_id"))
                success = data.get("success") is True
                current = load_agents()
                agent = agent_by_id(current["agents"], agent_id)
                agent["status"] = "ready" if success else "error"
                agent.pop("current_task", None)
                agent["last_result"] = str(data.get("summary", ""))[:300]
                for task in current.get("tasks", []):
                    if task.get("id") == task_id:
                        completed = task.setdefault("completed_agents", [])
                        if agent_id not in completed:
                            completed.append(agent_id)
                        if set(completed) >= set(task.get("agents", [])):
                            task["status"] = "completed"
                save_document(AGENTS_PATH, current)
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
    ThreadingHTTPServer(("127.0.0.1", CONTROL_PORT), Handler).serve_forever()
