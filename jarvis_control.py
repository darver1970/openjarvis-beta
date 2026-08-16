"""Lokální API pro trvalá pravidla JARVISu uložená výhradně na disku A:."""

import json
import logging
import os
import re
import shutil
import subprocess
import threading
import urllib.error
import urllib.request
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent
CONTROL_PORT = int(os.environ.get("JARVIS_CONTROL_PORT", "8126"))
RULES_PATH = ROOT / "runtime" / "jarvis-rules.json"
SETTINGS_PATH = ROOT / "runtime" / "jarvis-settings.json"
CLOUD_SECRETS_PATH = ROOT / "runtime" / "cloud-api-secrets.json"
PROVIDER_HEALTH_PATH = ROOT / "runtime" / "provider-health.json"
OPENCLAW_ROOT = ROOT / "runtime" / "openclaw"
OPENCLAW_BINARY = OPENCLAW_ROOT / "node_modules" / ".bin" / "openclaw.cmd"
OPENCLAW_ENTRYPOINT = OPENCLAW_ROOT / "node_modules" / "openclaw" / "openclaw.mjs"
OPENCLAW_CONFIG_PATH = OPENCLAW_ROOT / "openclaw.json"
OPENCLAW_STATE_DIR = OPENCLAW_ROOT / "state"
OPENCLAW_WORKSPACE = ROOT / "runtime" / "agents" / "openclaw"
VOICE_CONTROL_PATH = ROOT / "runtime" / "voice-control.json"
VOICE_CONFIG_PATH = ROOT / "runtime" / "voice-config.json"
VOICE_METER_PATH = ROOT / "runtime" / "voice-meter.json"
PROJECTS_PATH = ROOT / "runtime" / "jarvis-projects.json"
AGENTS_PATH = ROOT / "runtime" / "jarvis-agents.json"
DEFAULT_AGENTS_PATH = ROOT / "defaults" / "jarvis-agents.json"
AGENT_CATALOG_PATH = ROOT / "defaults" / "jarvis-agent-catalog.json"
PROJECT_MEMORY_PATH = ROOT / "runtime" / "jarvis-project-memory.json"
EXECUTIONS_DIR = ROOT / "runtime" / "executions"
LOG_PATH = ROOT / "runtime" / "jarvis-control.log"
PROJECT_MEMORY_LOCK = threading.Lock()
STARTUP_VALUE_NAME = "OpenJarvisBeta"
STARTUP_REGISTRY_PATH = r"HKCU:\Software\Microsoft\Windows\CurrentVersion\Run"
LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
EXECUTIONS_DIR.mkdir(parents=True, exist_ok=True)
logging.basicConfig(filename=LOG_PATH, level=logging.INFO, encoding="utf-8")

PROVIDERS: dict[str, dict[str, str]] = {
    "local": {"label": "Lokální Ollama", "model": ""},
    "gemini_free": {"label": "Gemini Free", "model": "gemini-3.5-flash"},
    "openrouter_free": {"label": "OpenRouter Free", "model": "openrouter/free"},
    "automatic": {"label": "Automaticky", "model": "bezplatný online router, pak lokální"},
}


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


def normalize_provider(value: object) -> str:
    """Přijímá pouze předem definované zdroje modelů."""
    provider = str(value or "local").strip().lower()
    if provider not in PROVIDERS:
        raise ValueError("Zvolený poskytovatel AI není podporován.")
    return provider


def load_cloud_secrets() -> dict[str, str]:
    """Načte pouze DPAPI šifrované hodnoty uložené mimo Git."""
    data = load_document(CLOUD_SECRETS_PATH, "providers")
    providers = data.get("providers", {})
    if not isinstance(providers, dict):
        return {}
    return {str(name): str(value) for name, value in providers.items() if name in PROVIDERS and isinstance(value, str)}


def protect_secret(value: str) -> str:
    """Zašifruje tajemství pomocí Windows DPAPI pro aktuální účet."""
    environment = os.environ.copy()
    environment["JARVIS_CLOUD_SECRET"] = value
    environment["PSModulePath"] = r"C:\Windows\System32\WindowsPowerShell\v1.0\Modules;C:\Program Files\WindowsPowerShell\Modules"
    result = subprocess.run(
        [r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe", "-NoProfile", "-NonInteractive", "-Command", "$s=ConvertTo-SecureString $env:JARVIS_CLOUD_SECRET -AsPlainText -Force; ConvertFrom-SecureString $s"],
        capture_output=True, text=True, encoding="utf-8", timeout=15, check=False, env=environment,
    )
    encrypted = result.stdout.strip()
    if result.returncode != 0 or not encrypted:
        raise ValueError("Windows nedokázal API klíč zašifrovat.")
    return encrypted


def unprotect_secret(value: str) -> str:
    """Rozšifruje klíč jen krátce pro jedno síťové volání stejného uživatele."""
    environment = os.environ.copy()
    environment["JARVIS_CLOUD_SECRET"] = value
    environment["PSModulePath"] = r"C:\Windows\System32\WindowsPowerShell\v1.0\Modules;C:\Program Files\WindowsPowerShell\Modules"
    script = (
        "$s=ConvertTo-SecureString $env:JARVIS_CLOUD_SECRET; "
        "$p=[Runtime.InteropServices.Marshal]::SecureStringToBSTR($s); "
        "try {[Runtime.InteropServices.Marshal]::PtrToStringBSTR($p)} "
        "finally {[Runtime.InteropServices.Marshal]::ZeroFreeBSTR($p)}"
    )
    result = subprocess.run(
        [r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
        capture_output=True, text=True, encoding="utf-8", timeout=15, check=False, env=environment,
    )
    secret = result.stdout.strip()
    if result.returncode != 0 or not secret:
        raise ValueError("API klíč nelze odemknout pro aktuální účet Windows.")
    return secret


def validate_cloud_secret(api_key: object) -> str:
    """Ověří formát klíče dříve, než se použije nebo uloží."""
    key = str(api_key or "").strip()
    if not 16 <= len(key) <= 512 or any(character.isspace() for character in key):
        raise ValueError("API klíč má neplatný formát.")
    return key


def save_cloud_secret(provider: str, api_key: object) -> None:
    """Uloží již ověřený klíč výhradně v DPAPI podobě."""
    key = validate_cloud_secret(api_key)
    secrets = load_cloud_secrets()
    secrets[provider] = protect_secret(key)
    save_document(CLOUD_SECRETS_PATH, {"providers": secrets})
    logging.info("Uložen šifrovaný API klíč poskytovatele: %s", provider)


def provider_status() -> dict[str, Any]:
    """Vrací stav providerů bez vystavení klíčů nebo šifrovaných dat."""
    secrets = load_cloud_secrets()
    return {"providers": [
        {"id": provider_id, "label": details["label"], "model": details["model"], "configured": provider_id in {"local", "automatic"} or provider_id in secrets}
        for provider_id, details in PROVIDERS.items()
    ]}


def automatic_provider_order() -> list[str]:
    """Vrátí bezplatné providery s Gemini jako výchozí první volbou."""
    secrets = load_cloud_secrets()
    return [provider for provider in ("gemini_free", "openrouter_free") if provider in secrets]


def record_provider_health(provider: str, succeeded: bool, started_at: datetime) -> None:
    """Uloží pouze výsledek a dobu odezvy, nikdy klíč ani obsah dotazu."""
    document = load_document(PROVIDER_HEALTH_PATH, "providers")
    providers = document.get("providers")
    if not isinstance(providers, dict):
        providers = {}
        document["providers"] = providers
    values = providers.setdefault(provider, {"successes": 0, "failures": 0, "average_ms": 0.0})
    elapsed_ms = max(1, int((datetime.now() - started_at).total_seconds() * 1000))
    if succeeded:
        previous = int(values.get("successes", 0))
        average = float(values.get("average_ms", 0.0))
        values["average_ms"] = round((average * previous + elapsed_ms) / (previous + 1), 1)
        values["successes"] = previous + 1
        values["last_success"] = datetime.now().isoformat(timespec="seconds")
    else:
        values["failures"] = int(values.get("failures", 0)) + 1
        values["last_failure"] = datetime.now().isoformat(timespec="seconds")
    save_document(PROVIDER_HEALTH_PATH, document)


def automatic_provider_request(messages: list[dict[str, object]], model: str = "") -> tuple[str, str]:
    """Použije bezplatný online zdroj a při vyčerpání či chybě přejde na lokální model."""
    for provider in automatic_provider_order():
        started_at = datetime.now()
        try:
            answer = provider_request(provider, messages, model)
            record_provider_health(provider, True, started_at)
            return provider, answer
        except ValueError as error:
            record_provider_health(provider, False, started_at)
            logging.warning("Automatický provider %s selhal, zkouším další: %s", provider, error)
    return "local", local_model_request(messages, model)


def local_model_request(messages: list[dict[str, object]], model: str) -> str:
    """Zachová lokální Ollama chat při přepnutí přes jednotnou bránu."""
    payload = {"model": model[:120] or "qwen3.5:4b", "messages": messages[-16:], "stream": False}
    request = urllib.request.Request(
        "http://127.0.0.1:8000/v1/chat/completions",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"}, method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=90) as response:
            data = json.loads(response.read().decode("utf-8"))
        answer = str(data["choices"][0]["message"]["content"])
    except (urllib.error.URLError, KeyError, IndexError, TypeError, json.JSONDecodeError) as error:
        raise ValueError("Lokální Ollama není dostupná.") from error
    return answer.strip() or "Lokální model nevrátil odpověď."


def run_openclaw_agent(task: str) -> str:
    """Spustí jen lokální textovou inference OpenClaw bez Gateway a nástrojů."""
    node_binary = shutil.which("node.exe")
    if not node_binary or not OPENCLAW_ENTRYPOINT.is_file() or not OPENCLAW_CONFIG_PATH.is_file():
        raise ValueError("OpenClaw není lokálně nainstalovaný.")
    environment = os.environ.copy()
    environment["OPENCLAW_STATE_DIR"] = str(OPENCLAW_STATE_DIR)
    environment["OPENCLAW_CONFIG_PATH"] = str(OPENCLAW_CONFIG_PATH)
    environment["OPENCLAW_WORKSPACE_DIR"] = str(OPENCLAW_WORKSPACE)
    prompt = (
        "Jsi OpenClaw, lokální pomocný agent JARVISu. "
        "Neprováděj příkazy, neotevírej síť, neměň soubory a nenavrhuj obcházení schválení. "
        "Odpověz česky stručným výsledkem nebo bezpečným návrhem postupu.\n\n"
        f"Úkol: {task}"
    )
    try:
        result = subprocess.run(
            [node_binary, str(OPENCLAW_ENTRYPOINT), "infer", "model", "run", "--local", "--model", "ollama/qwen2.5-coder:7b", "--prompt", prompt, "--json"],
            capture_output=True, text=True, encoding="utf-8", timeout=180, check=False, env=environment,
        )
        if result.returncode != 0:
            raise ValueError((result.stderr or "OpenClaw nevrátil úspěšný stav.").strip()[:800])
        payload = json.loads(result.stdout)
        output = str(payload.get("outputs", [{}])[0].get("text", "")).strip()
    except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError, IndexError, TypeError) as error:
        logging.warning("OpenClaw inference selhala: %s", error)
        raise ValueError("OpenClaw nedokončil lokální úkol.") from error
    if not output:
        raise ValueError("OpenClaw nevrátil textovou odpověď.")
    logging.info("OpenClaw dokončil lokální úkol o délce %s", len(task))
    return output[:12000]


def provider_request(
    provider: str,
    messages: list[dict[str, object]],
    model: str = "",
    api_key: str | None = None,
) -> str:
    """Odešle explicitně zvolený online chat a vrátí pouze odpověď modelu."""
    if provider == "local":
        return local_model_request(messages, model)
    if api_key is None:
        encrypted = load_cloud_secrets().get(provider)
        if not encrypted:
            raise ValueError("Pro zvolený online režim nejdříve vložte API klíč.")
        api_key = unprotect_secret(encrypted)
    else:
        api_key = validate_cloud_secret(api_key)
    sanitized = [{"role": str(item.get("role", "user")), "content": str(item.get("content", ""))[:5000]} for item in messages[-16:] if isinstance(item, dict) and str(item.get("content", "")).strip()]
    if not sanitized:
        raise ValueError("Online dotaz neobsahuje žádnou zprávu.")
    try:
        if provider == "gemini_free":
            system = "\n".join(item["content"] for item in sanitized if item["role"] == "system")
            contents = [{"role": "model" if item["role"] == "assistant" else "user", "parts": [{"text": item["content"]}]} for item in sanitized if item["role"] != "system"]
            payload: dict[str, Any] = {"contents": contents, "generationConfig": {"maxOutputTokens": 1200}}
            if system:
                payload["systemInstruction"] = {"parts": [{"text": system[:5000]}]}
            request = urllib.request.Request("https://generativelanguage.googleapis.com/v1beta/models/gemini-3.5-flash:generateContent", data=json.dumps(payload, ensure_ascii=False).encode("utf-8"), headers={"Content-Type": "application/json", "x-goog-api-key": api_key}, method="POST")
            with urllib.request.urlopen(request, timeout=45) as response:
                data = json.loads(response.read().decode("utf-8"))
            answer = "".join(str(part.get("text", "")) for part in data["candidates"][0]["content"]["parts"])
        elif provider == "openrouter_free":
            request = urllib.request.Request("https://openrouter.ai/api/v1/chat/completions", data=json.dumps({"model": "openrouter/free", "messages": sanitized, "max_tokens": 1200}, ensure_ascii=False).encode("utf-8"), headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"}, method="POST")
            with urllib.request.urlopen(request, timeout=45) as response:
                data = json.loads(response.read().decode("utf-8"))
            answer = str(data["choices"][0]["message"]["content"])
        else:
            raise ValueError("Lokální model se online branou nepoužívá.")
    except urllib.error.HTTPError as error:
        logging.warning("Online API %s vrátilo %s", provider, error.code)
        if provider == "gemini_free" and error.code == 404:
            raise ValueError("Gemini model není pro tento účet dostupný. Zkontrolujte model nebo klíč.") from error
        raise ValueError(f"Online API vrátilo {error.code}. Ověřte klíč nebo bezplatnou kvótu.") from error
    except (urllib.error.URLError, TimeoutError) as error:
        raise ValueError("Online API není dostupné. Zkontrolujte internetové připojení.") from error
    if not answer.strip():
        raise ValueError("Online model nevrátil textovou odpověď.")
    return answer.strip()


def list_audio_inputs() -> list[dict[str, str]]:
    """Vrátí dostupné vstupy bez záznamu zvuku a bez přístupu mimo počítač."""
    try:
        import sounddevice as sd

        allowed = re.compile(r"mikrofon|microphone|headset|hands-free", re.IGNORECASE)
        blocked = re.compile(r"mapper|steam|stereo|line|kabel|cable|primární|primary", re.IGNORECASE)
        return [
            {"id": str(index), "name": str(device["name"])}
            for index, device in enumerate(sd.query_devices())
            if int(device.get("max_input_channels", 0)) > 0
            and allowed.search(str(device["name"]))
            and not blocked.search(str(device["name"]))
        ]
    except Exception as error:
        logging.warning("Seznam mikrofonů nelze načíst: %s", error)
        return []


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


def validate_powershell_command(value: object) -> str:
    """Přijímá pouze omezeně dlouhý explicitně potvrzený PowerShell příkaz."""
    command = str(value or "").strip()
    if not 1 <= len(command) <= 6000 or "\x00" in command:
        raise ValueError("PowerShell příkaz musí mít 1 až 6000 platných znaků.")
    return command


def command_result(result: subprocess.CompletedProcess[str], elevated: bool) -> dict[str, Any]:
    """Normalizuje výsledek PowerShellu bez neomezeného vracení výstupu."""
    output = (result.stdout or "") + (result.stderr or "")
    return {
        "elevated": elevated,
        "exit_code": result.returncode,
        "output": output.strip()[:24000],
    }


def run_powershell(command: str, elevated: bool) -> dict[str, Any]:
    """Spustí potvrzený příkaz; elevace vždy prochází Windows UAC."""
    if not elevated:
        result = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", command],
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=120,
            check=False,
        )
        return command_result(result, elevated=False)

    execution_id = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    command_path = EXECUTIONS_DIR / f"{execution_id}.ps1"
    output_path = EXECUTIONS_DIR / f"{execution_id}.out.txt"
    status_path = EXECUTIONS_DIR / f"{execution_id}.status.json"
    runner_path = EXECUTIONS_DIR / f"{execution_id}.runner.ps1"
    command_path.write_text(command, encoding="utf-8")
    command_literal = str(command_path).replace("'", "''")
    output_literal = str(output_path).replace("'", "''")
    status_literal = str(status_path).replace("'", "''")
    runner_literal = str(runner_path).replace("'", "''")
    runner_script = (
        "$ErrorActionPreference = 'Stop'\n"
        f"$commandPath = '{command_literal}'\n"
        f"$outputPath = '{output_literal}'\n"
        f"$statusPath = '{status_literal}'\n"
        "try {\n"
        "    & powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass -File $commandPath *>&1 |\n"
        "        Out-File -LiteralPath $outputPath -Encoding utf8\n"
        "    $status = @{ exit_code = $LASTEXITCODE; error = '' }\n"
        "} catch {\n"
        "    $_ | Out-File -LiteralPath $outputPath -Encoding utf8 -Append\n"
        "    $status = @{ exit_code = 1; error = $_.Exception.Message }\n"
        "}\n"
        "$status | ConvertTo-Json -Compress | Set-Content -LiteralPath $statusPath -Encoding utf8\n"
        "exit 0\n"
    )
    runner_path.write_text(runner_script, encoding="utf-8")
    environment = os.environ.copy()
    launcher = (
        "$ErrorActionPreference = 'Stop'; "
        "try { "
        "$process = Start-Process -FilePath 'powershell.exe' -Verb RunAs -PassThru -Wait "
        f"-ArgumentList @('-NoProfile','-NonInteractive','-ExecutionPolicy','Bypass','-File','{runner_literal}'); "
        "if ($null -eq $process) { throw 'Administrátorský proces nebyl vytvořen.' }; "
        "exit $process.ExitCode "
        "} catch { [Console]::Error.WriteLine($_.Exception.Message); exit 1 }"
    )
    result = subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", launcher],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=180,
        check=False,
        env=environment,
    )
    if not status_path.exists():
        detail = (result.stderr or result.stdout or "UAC bylo zamítnuto nebo administrátorský proces nelze spustit.").strip()
        return {"elevated": True, "exit_code": result.returncode or 1, "output": detail[:24000]}
    try:
        status = json.loads(status_path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        status = {"exit_code": 1, "error": "Administrátorský stavový soubor nelze načíst."}
    output = output_path.read_text(encoding="utf-8-sig", errors="replace") if output_path.exists() else ""
    return {
        "elevated": True,
        "exit_code": int(status.get("exit_code", 1)),
        "output": (output or str(status.get("error", ""))).strip()[:24000],
        "execution_id": execution_id,
    }


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
            settings["ai_provider"] = normalize_provider(settings.get("ai_provider", "local"))
            settings["start_with_windows"] = startup_is_enabled()
            self.send_json(settings)
        elif self.path == "/providers":
            self.send_json(provider_status())
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
            state.setdefault("manual_listen", False)
            self.send_json(state)
        elif self.path == "/voice/meter":
            self.send_json(load_document(VOICE_METER_PATH, "meter"))
        elif self.path == "/audio/devices":
            voice_config = load_document(VOICE_CONFIG_PATH, "voice")
            self.send_json({"inputs": list_audio_inputs(), "selected_input": str(voice_config.get("input_device", ""))})
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
            if self.path == "/powershell/execute":
                if data.get("confirmed") is not True:
                    raise ValueError("Spuštění PowerShellu vyžaduje potvrzení v HUDu.")
                command = validate_powershell_command(data.get("command"))
                elevated = data.get("elevated") is True
                result = run_powershell(command, elevated)
                logging.info(
                    "PowerShell spuštěn: elevated=%s, exit_code=%s, příkaz=%s",
                    elevated,
                    result["exit_code"],
                    command[:200],
                )
                self.send_json(result)
                return
            if self.path == "/settings":
                allowed = {
                    "default_model", "voice_output", "wake_word", "internet_mode",
                    "project_start_required", "start_with_windows", "borderless_window", "powershell_uac", "ai_provider",
                }
                current = load_document(SETTINGS_PATH, "settings")
                for key, value in data.items():
                    if key in allowed:
                        current[key] = value
                current["ai_provider"] = normalize_provider(current.get("ai_provider", "local"))
                current["cloud_api"] = current["ai_provider"] != "local"
                if "start_with_windows" in data:
                    if not isinstance(data["start_with_windows"], bool):
                        raise ValueError("Automatické spuštění musí mít hodnotu ano nebo ne.")
                    current["start_with_windows"] = set_startup_enabled(data["start_with_windows"])
                save_document(SETTINGS_PATH, current)
                self.send_json(current)
                return
            if self.path == "/providers/key":
                provider = normalize_provider(data.get("provider"))
                if provider == "local":
                    raise ValueError("Lokální režim API klíč nepoužívá.")
                api_key = validate_cloud_secret(data.get("api_key"))
                if data.get("test") is True:
                    provider_request(provider, [{"role": "user", "content": "Odpověz pouze OK."}], api_key=api_key)
                save_cloud_secret(provider, api_key)
                if provider == "gemini_free":
                    voice_config = load_document(VOICE_CONFIG_PATH, "voice")
                    voice_config["gemini_live_preferred"] = True
                    save_document(VOICE_CONFIG_PATH, voice_config)
                self.send_json(provider_status())
                return
            if self.path == "/chat":
                settings = load_document(SETTINGS_PATH, "settings")
                provider = normalize_provider(settings.get("ai_provider", "local"))
                messages = data.get("messages", [])
                if not isinstance(messages, list):
                    raise ValueError("Zprávy pro online model mají neplatný formát.")
                if provider == "automatic":
                    selected_provider, answer = automatic_provider_request(messages, str(data.get("model", "")))
                else:
                    selected_provider = provider
                    answer = provider_request(selected_provider, messages, str(data.get("model", "")))
                self.send_json({"provider": selected_provider, "answer": answer})
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
            if self.path == "/audio/input":
                selected_input = str(data.get("input_device", "")).strip()
                inputs = list_audio_inputs()
                selected = next((item for item in inputs if item["id"] == selected_input), None)
                if selected is None:
                    raise ValueError("Vybraný mikrofon již není dostupný.")
                config = load_document(VOICE_CONFIG_PATH, "voice")
                config["input_device"] = selected["name"]
                save_document(VOICE_CONFIG_PATH, config)
                logging.info("Vybrán mikrofon JARVISu: %s", selected["name"])
                self.send_json({"input_device": selected["name"], "restart_required": True})
                return
            if self.path == "/voice/stop":
                state = load_document(VOICE_CONTROL_PATH, "voice")
                state["enabled"] = True
                state["cancel_requested"] = True
                state["manual_listen"] = False
                save_document(VOICE_CONTROL_PATH, state)
                self.send_json(state)
                return
            if self.path == "/voice/listen":
                state = load_document(VOICE_CONTROL_PATH, "voice")
                state["enabled"] = True
                state["cancel_requested"] = False
                state["manual_listen"] = True
                save_document(VOICE_CONTROL_PATH, state)
                self.send_json(state)
                return
            if self.path == "/agents/activate":
                agent_id = normalize_agent_id(data.get("agent_id"))
                current = load_agents()
                agent = agent_by_id(current["agents"], agent_id)
                if agent.get("status") == "planned":
                    raise ValueError("Tento modul zatím není nainstalovaný ani připravený ke spuštění.")
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
                if agent.get("status") == "planned":
                    raise ValueError("Neinstalovaný modul nelze spustit ani pozastavit.")
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
            if self.path == "/agents/openclaw/run":
                task = str(data.get("task", "")).strip()
                if not 1 <= len(task) <= 2000:
                    raise ValueError("Úkol pro OpenClaw musí mít 1 až 2000 znaků.")
                current = load_agents()
                agent = agent_by_id(current["agents"], "openclaw")
                if agent.get("status") != "ready":
                    raise ValueError("OpenClaw není připravený k lokálnímu úkolu.")
                self.send_json({"answer": run_openclaw_agent(task), "provider": "openclaw-local"})
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
