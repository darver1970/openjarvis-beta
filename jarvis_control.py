"""Lokální API pro trvalá pravidla JARVISu uložená v instalační složce."""

import json
import csv
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
ACTIVE_PROVIDER_PATH = ROOT / "runtime" / "active-provider.json"
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
TELEMETRY_SETTINGS_PATH = ROOT / "runtime" / "telemetry-settings.json"
TELEMETRY_OUTPUT_DIR = ROOT / "runtime" / "telemetry"
HARDWARE_STATUS_PATH = ROOT / "hud" / "hardware-status.json"
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

TELEMETRY_CATEGORIES = [
    {"id": "core", "label": "Základní měření", "description": "Nízká režie; doporučené pro běžný provoz."},
    {"id": "extended", "label": "Rozšířená diagnostika", "description": "Podrobnější údaje o procesech a komponentách."},
    {"id": "history", "label": "Historie a upozornění", "description": "Dlouhodobé trendy, limity a automatická upozornění."},
    {"id": "security", "label": "Síť a bezpečnost", "description": "Kontrola spojení, podpisů a neobvyklého chování."},
    {"id": "gaming", "label": "Hry a grafika", "description": "FPS, frametime a detailní údaje GPU."},
    {"id": "reporting", "label": "Výstupy a porovnání", "description": "Exporty, diagnostické snímky a porovnání běhů."},
]

TELEMETRY_FEATURES = [
    {"id": "process_monitoring", "category": "core", "label": "Seznam procesů", "description": "CPU, paměť a stav všech procesů.", "status": "active", "default": True},
    {"id": "process_disk_io", "category": "core", "label": "Disk procesů", "description": "Rychlost čtení a zápisu každého procesu.", "status": "active", "default": True},
    {"id": "process_network_connections", "category": "core", "label": "Síťová spojení procesů", "description": "Počet aktivních internetových spojení podle PID.", "status": "active", "default": True},
    {"id": "process_gpu", "category": "core", "label": "GPU procesů", "description": "Vytížení GPU enginů podle procesu.", "status": "active", "default": True},
    {"id": "hardware_sensors", "category": "core", "label": "Hardwarové senzory", "description": "CPU, RAM, GPU, disky, síť a jejich souhrnné zatížení.", "status": "active", "default": True},
    {"id": "temperatures", "category": "core", "label": "Teploty", "description": "Živé teploty dostupné přes LibreHardwareMonitor.", "status": "active", "default": True},
    {"id": "process_details", "category": "core", "label": "Podrobnosti procesu", "description": "Uživatel, cesta, PID, vlákna a handly.", "status": "active", "default": True},
    {"id": "process_grouping", "category": "core", "label": "Seskupování aplikací", "description": "Spojí stejné procesy do rozbalitelných skupin.", "status": "active", "default": True},
    {"id": "process_termination", "category": "core", "label": "Ukončení procesu", "description": "Povolí tlačítko Ukončit úlohu s potvrzením.", "status": "active", "default": True},
    {"id": "process_tree", "category": "extended", "label": "Strom procesů", "description": "Rodičovské a podřízené procesy v hierarchii.", "status": "prepared", "default": False},
    {"id": "windows_services", "category": "extended", "label": "Služby procesu", "description": "Přiřazení služeb Windows k hostitelským procesům.", "status": "prepared", "default": False, "requires_admin": True},
    {"id": "startup_impact", "category": "extended", "label": "Dopad po spuštění", "description": "Čas startu Windows a dopad automaticky spouštěných aplikací.", "status": "prepared", "default": False},
    {"id": "energy_usage", "category": "extended", "label": "Spotřeba energie", "description": "Příkon a energetický dopad procesů a komponent.", "status": "prepared", "default": False},
    {"id": "page_faults", "category": "extended", "label": "Stránkování paměti", "description": "Hard faults, cache a zatížení stránkovacího souboru.", "status": "prepared", "default": False},
    {"id": "smart_storage", "category": "extended", "label": "SMART a životnost disků", "description": "Zdraví, životnost SSD a množství zapsaných dat.", "status": "prepared", "default": False, "requires_admin": True},
    {"id": "fan_voltage_clocks", "category": "extended", "label": "Ventilátory, napětí a frekvence", "description": "Rozšířené senzory chlazení a taktování.", "status": "prepared", "default": False},
    {"id": "history_charts", "category": "history", "label": "Historické grafy", "description": "Minuty, hodiny a dny vývoje vytížení a teplot.", "status": "prepared", "default": False},
    {"id": "threshold_alerts", "category": "history", "label": "Upozornění na limity", "description": "Teploty, RAM, disk, síť a dlouhodobé vysoké vytížení.", "status": "prepared", "default": False},
    {"id": "anomaly_detection", "category": "history", "label": "Detekce neobvyklého stavu", "description": "Porovná aktuální chování s běžným stavem počítače.", "status": "prepared", "default": False},
    {"id": "memory_leak_detection", "category": "history", "label": "Úniky paměti", "description": "Sleduje dlouhodobě rostoucí spotřebu RAM procesu.", "status": "prepared", "default": False},
    {"id": "thermal_throttling", "category": "history", "label": "Throttling", "description": "Rozpozná teplotní nebo výkonové omezení CPU a GPU.", "status": "prepared", "default": False},
    {"id": "etw_network_speed", "category": "security", "label": "Rozšířená síť procesu", "description": "Lokální přehled spojení procesu; dostupnost systémových detailů závisí na oprávnění Windows.", "status": "prepared", "default": False, "requires_admin": True},
    {"id": "connection_details", "category": "security", "label": "Cílové adresy a porty", "description": "IP adresy, porty, protokoly a stav spojení procesů.", "status": "prepared", "default": False, "requires_admin": True},
    {"id": "publisher_signatures", "category": "security", "label": "Podpis a vydavatel", "description": "Ověření digitálního podpisu spustitelného souboru.", "status": "prepared", "default": False},
    {"id": "file_hashes", "category": "security", "label": "Kontrolní hashe", "description": "Lokální SHA-256 identifikace programů.", "status": "prepared", "default": False},
    {"id": "open_files_registry", "category": "security", "label": "Otevřené soubory a registry", "description": "Soubory, knihovny a registry používané procesem.", "status": "prepared", "default": False, "requires_admin": True},
    {"id": "remote_location", "category": "security", "label": "Typ vzdálené sítě", "description": "Lokální rozlišení veřejné, privátní nebo neznámé vzdálené IP adresy.", "status": "prepared", "default": False},
    {"id": "fps_monitoring", "category": "gaming", "label": "FPS", "description": "Snímková frekvence právě spuštěné hry.", "status": "prepared", "default": False, "requires_admin": True},
    {"id": "frametime_monitoring", "category": "gaming", "label": "Frametime a propady", "description": "Plynulost vykreslování a detekce záseků.", "status": "prepared", "default": False, "requires_admin": True},
    {"id": "gpu_vram_details", "category": "gaming", "label": "VRAM a GPU enginy", "description": "Dedikovaná a sdílená VRAM, 3D, Copy, Compute a Video.", "status": "prepared", "default": False},
    {"id": "game_session_compare", "category": "gaming", "label": "Porovnání herního běhu", "description": "Porovná výkon před spuštěním, během hry a po ukončení.", "status": "prepared", "default": False},
    {"id": "diagnostic_export", "category": "reporting", "label": "Export JSON a CSV", "description": "Lokální export bez API klíčů a osobních tajemství.", "status": "prepared", "default": False},
    {"id": "diagnostic_snapshots", "category": "reporting", "label": "Diagnostické snímky", "description": "Uloží stav počítače pro pozdější porovnání.", "status": "prepared", "default": False},
    {"id": "before_after_compare", "category": "reporting", "label": "Porovnání před a po", "description": "Změny vytížení způsobené zvoleným programem.", "status": "prepared", "default": False},
    {"id": "jarvis_usage_separation", "category": "reporting", "label": "Spotřeba samotného Jarvise", "description": "Oddělí procesy Jarvise od ostatních programů.", "status": "prepared", "default": False},
    {"id": "process_priority_affinity", "category": "extended", "label": "Priorita a afinita CPU", "description": "Zobrazení a bezpečná změna priority nebo přiřazených jader.", "status": "prepared", "default": False, "requires_admin": True},
    {"id": "process_suspend_resume", "category": "extended", "label": "Pozastavit a pokračovat", "description": "Dočasné pozastavení procesu bez jeho ukončení.", "status": "prepared", "default": False, "requires_admin": True},
    {"id": "user_sessions", "category": "extended", "label": "Vytížení podle uživatele", "description": "Souhrn prostředků podle přihlášených účtů Windows.", "status": "prepared", "default": False},
    {"id": "application_icons", "category": "extended", "label": "Ikony a názvy aplikací", "description": "Načte originální ikonu a popis programu z EXE souboru.", "status": "prepared", "default": False},
    {"id": "disk_queue_latency", "category": "extended", "label": "Odezva a fronta disku", "description": "Latence operací a délka fronty každého fyzického disku.", "status": "prepared", "default": False},
    {"id": "network_adapter_split", "category": "extended", "label": "Síť podle adaptéru", "description": "Oddělí Ethernet, Wi-Fi, VPN, LAN a internetový provoz.", "status": "prepared", "default": False},
    {"id": "fan_curves", "category": "extended", "label": "Křivky ventilátorů", "description": "Historie otáček podle teploty; bez automatické změny BIOSu.", "status": "prepared", "default": False},
    {"id": "sustained_disk_writes", "category": "history", "label": "Dlouhodobé zápisy na disk", "description": "Upozorní na proces trvale zapisující velké množství dat.", "status": "prepared", "default": False},
    {"id": "bottleneck_advice", "category": "history", "label": "Lokální hledání úzkého hrdla", "description": "Určí, zda výkon omezuje CPU, RAM, disk, síť nebo GPU.", "status": "prepared", "default": False},
    {"id": "unsigned_process_alerts", "category": "security", "label": "Upozornění na nepodepsané procesy", "description": "Zvýrazní nové programy bez platného digitálního podpisu.", "status": "prepared", "default": False},
    {"id": "safe_close_before_kill", "category": "security", "label": "Bezpečné zavření před ukončením", "description": "Nejdříve požádá aplikaci o zavření, teprve potom nabídne vynucení.", "status": "prepared", "default": False},
    {"id": "second_monitor_dashboard", "category": "reporting", "label": "Panel pro druhý monitor", "description": "Samostatný celoobrazovkový přehled výkonu a grafů.", "status": "prepared", "default": False},
]

# Každá funkce je navázaná na konkrétní lokální sběrač nebo bezpečnou akci.
TELEMETRY_IMPLEMENTATIONS = {
    **{key: "hardware_monitor" for key in (
        "process_monitoring", "process_disk_io", "process_network_connections", "process_gpu",
        "hardware_sensors", "temperatures", "process_details", "process_grouping", "process_tree",
        "windows_services", "startup_impact", "energy_usage", "page_faults", "smart_storage",
        "fan_voltage_clocks", "user_sessions", "application_icons", "disk_queue_latency",
        "network_adapter_split", "gpu_vram_details", "jarvis_usage_separation",
    )},
    **{key: "history_engine" for key in (
        "history_charts", "threshold_alerts", "anomaly_detection", "memory_leak_detection",
        "thermal_throttling", "fan_curves", "sustained_disk_writes", "bottleneck_advice",
        "game_session_compare",
    )},
    **{key: "security_collector" for key in (
        "etw_network_speed", "connection_details", "publisher_signatures", "file_hashes",
        "open_files_registry", "remote_location", "unsigned_process_alerts",
    )},
    "fps_monitoring": "presentmon", "frametime_monitoring": "presentmon",
    **{key: "control_api" for key in (
        "process_termination", "process_priority_affinity", "process_suspend_resume", "safe_close_before_kill",
        "diagnostic_export", "diagnostic_snapshots", "before_after_compare",
    )},
    "second_monitor_dashboard": "native_hud",
}
for _telemetry_feature in TELEMETRY_FEATURES:
    implementation = TELEMETRY_IMPLEMENTATIONS.get(str(_telemetry_feature.get("id")))
    if implementation:
        _telemetry_feature["status"] = "active"
        _telemetry_feature["implementation"] = implementation


class ProviderQuotaError(ValueError):
    """Provider dosáhl bezplatné kvóty a automatický režim smí přepnout dál."""


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
    """Zapíše dokument atomicky výhradně do runtime adresáře instalace."""
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def telemetry_default_settings() -> dict[str, Any]:
    """Sestaví bezpečné výchozí nastavení; náročné připravené funkce jsou vypnuté."""
    return {
        "enabled": True,
        "sampling_seconds": 2,
        "features": {feature["id"]: bool(feature["default"]) for feature in TELEMETRY_FEATURES},
    }


def load_telemetry_settings() -> dict[str, Any]:
    """Načte telemetrii se sloučením nových voleb do staršího nastavení."""
    defaults = telemetry_default_settings()
    data = load_document(TELEMETRY_SETTINGS_PATH, "features")
    settings = {
        "enabled": data.get("enabled", defaults["enabled"]) is True,
        "sampling_seconds": int(data.get("sampling_seconds", defaults["sampling_seconds"])),
        "features": defaults["features"].copy(),
    }
    if settings["sampling_seconds"] not in {1, 2, 5, 10}:
        settings["sampling_seconds"] = 2
    incoming = data.get("features", {})
    if isinstance(incoming, dict):
        for feature_id in settings["features"]:
            if feature_id in incoming and isinstance(incoming[feature_id], bool):
                settings["features"][feature_id] = incoming[feature_id]
    return settings


def telemetry_settings_payload() -> dict[str, Any]:
    settings = load_telemetry_settings()
    return {"settings": settings, "categories": TELEMETRY_CATEGORIES, "features": TELEMETRY_FEATURES}


def update_telemetry_settings(data: dict[str, Any]) -> dict[str, Any]:
    """Uloží pouze známé booleany a povolené intervaly vzorkování."""
    settings = load_telemetry_settings()
    if "enabled" in data:
        if not isinstance(data["enabled"], bool):
            raise ValueError("Hlavní přepínač telemetrie musí být ano nebo ne.")
        settings["enabled"] = data["enabled"]
    if "sampling_seconds" in data:
        interval = int(data["sampling_seconds"])
        if interval not in {1, 2, 5, 10}:
            raise ValueError("Interval telemetrie musí být 1, 2, 5 nebo 10 sekund.")
        settings["sampling_seconds"] = interval
    incoming = data.get("features", {})
    if incoming is not None:
        if not isinstance(incoming, dict):
            raise ValueError("Seznam funkcí telemetrie má neplatný formát.")
        for feature_id, enabled in incoming.items():
            if feature_id not in settings["features"]:
                raise ValueError("Nastavení obsahuje neznámou funkci telemetrie.")
            if not isinstance(enabled, bool):
                raise ValueError("Přepínač funkce telemetrie musí být ano nebo ne.")
            settings["features"][feature_id] = enabled
    save_document(TELEMETRY_SETTINGS_PATH, settings)
    return telemetry_settings_payload()


def hardware_status() -> dict[str, Any]:
    """Načte poslední lokální snímek bez konfigurace a API tajemství."""
    data = load_document(HARDWARE_STATUS_PATH, "system_usage")
    return data if isinstance(data, dict) else {}


def telemetry_output_path(prefix: str, suffix: str) -> Path:
    TELEMETRY_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    return TELEMETRY_OUTPUT_DIR / f"{prefix}-{stamp}.{suffix}"


def save_telemetry_snapshot(label: str = "snapshot") -> dict[str, Any]:
    status = hardware_status()
    path = telemetry_output_path(re.sub(r"[^a-zA-Z0-9_-]+", "-", label).strip("-") or "snapshot", "json")
    save_document(path, status)
    return {"saved": True, "path": str(path), "snapshot": status}


def export_telemetry(file_format: str) -> dict[str, Any]:
    status = hardware_status()
    if file_format == "json":
        path = telemetry_output_path("telemetry-export", "json")
        save_document(path, status)
    elif file_format == "csv":
        path = telemetry_output_path("telemetry-export", "csv")
        rows = status.get("processes", []) if isinstance(status.get("processes"), list) else []
        allowed = ["name", "pid", "cpu_percent", "memory_mb", "disk_mbps", "gpu_percent", "network_connections", "username"]
        with path.open("w", encoding="utf-8-sig", newline="") as target:
            writer = csv.DictWriter(target, fieldnames=allowed, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(rows)
    else:
        raise ValueError("Export podporuje pouze JSON nebo CSV.")
    return {"exported": True, "format": file_format, "path": str(path)}


def telemetry_comparison(phase: str) -> dict[str, Any]:
    TELEMETRY_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    baseline_path = TELEMETRY_OUTPUT_DIR / "comparison-baseline.json"
    current = hardware_status()
    if phase == "baseline":
        save_document(baseline_path, current)
        return {"baseline_saved": True, "path": str(baseline_path)}
    if phase != "compare":
        raise ValueError("Porovnání očekává fázi baseline nebo compare.")
    baseline = load_document(baseline_path, "system_usage")
    if not baseline_path.is_file():
        raise ValueError("Nejdříve ulož výchozí snímek před měřením.")
    before = baseline.get("system_usage", {})
    after = current.get("system_usage", {})
    keys = ("cpu_percent", "memory_percent", "disk_percent", "network_percent", "network_mbps")
    delta = {key: round(float(after.get(key) or 0) - float(before.get(key) or 0), 2) for key in keys}
    result = {"created_at": datetime.now().isoformat(), "before": before, "after": after, "delta": delta}
    path = telemetry_output_path("comparison", "json")
    save_document(path, result)
    return {"compared": True, "path": str(path), "result": result}


def validate_manageable_pid(pid: int) -> None:
    if pid <= 4 or pid == os.getpid():
        raise ValueError("Tento systémový proces nelze spravovat.")
    protected = {"system", "registry", "smss", "csrss", "wininit", "winlogon", "services", "lsass"}
    script = f"(Get-Process -Id {pid} -ErrorAction Stop).ProcessName"
    result = subprocess.run(["powershell", "-NoProfile", "-Command", script], capture_output=True, text=True, timeout=6)
    if result.returncode != 0 or result.stdout.strip().lower() in protected:
        raise ValueError("Chráněný nebo neexistující proces Windows.")


def manage_process(data: dict[str, Any]) -> dict[str, Any]:
    settings = load_telemetry_settings()["features"]
    pid = int(data.get("pid", 0))
    action = str(data.get("action", ""))
    validate_manageable_pid(pid)
    if action in {"suspend", "resume"}:
        if settings.get("process_suspend_resume") is not True:
            raise ValueError("Pozastavení procesů je vypnuté v telemetrii.")
        method = "NtSuspendProcess" if action == "suspend" else "NtResumeProcess"
        command = (
            "Add-Type -TypeDefinition 'using System; using System.Runtime.InteropServices; public static class JarvisNative {"
            "[DllImport(\"ntdll.dll\")] public static extern int NtSuspendProcess(IntPtr h);"
            "[DllImport(\"ntdll.dll\")] public static extern int NtResumeProcess(IntPtr h); }'; "
            f"$p=Get-Process -Id {pid} -ErrorAction Stop; $result=[JarvisNative]::{method}($p.Handle); if($result -ne 0){{exit $result}}"
        )
    elif action == "priority":
        if settings.get("process_priority_affinity") is not True:
            raise ValueError("Změna priority je vypnutá v telemetrii.")
        priority = str(data.get("priority", "Normal"))
        if priority not in {"Idle", "BelowNormal", "Normal", "AboveNormal", "High"}:
            raise ValueError("Nepovolená priorita procesu.")
        command = f"(Get-Process -Id {pid} -ErrorAction Stop).PriorityClass='{priority}'"
    elif action == "affinity":
        if settings.get("process_priority_affinity") is not True:
            raise ValueError("Změna afinity je vypnutá v telemetrii.")
        mask = int(data.get("mask", 0))
        if mask <= 0 or mask >= 2 ** max(os.cpu_count() or 1, 1):
            raise ValueError("Neplatná maska procesorových jader.")
        command = f"(Get-Process -Id {pid} -ErrorAction Stop).ProcessorAffinity={mask}"
    elif action == "safe_close":
        if settings.get("safe_close_before_kill") is not True:
            raise ValueError("Bezpečné zavření je vypnuté v telemetrii.")
        command = f"$p=Get-Process -Id {pid} -ErrorAction Stop; if(-not $p.CloseMainWindow()){{exit 2}}"
    else:
        raise ValueError("Neznámá akce procesu.")
    result = subprocess.run(["powershell", "-NoProfile", "-Command", command], capture_output=True, text=True, encoding="utf-8", timeout=10)
    if result.returncode != 0:
        raise ValueError((result.stderr or "Akci procesu nelze provést.").strip())
    return {"pid": pid, "action": action, "completed": True}


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


def active_provider_status() -> dict[str, str]:
    """Načte posledního úspěšného poskytovatele bez historie dotazů a klíčů."""
    data = load_document(ACTIVE_PROVIDER_PATH, "provider")
    provider = str(data.get("provider", ""))
    if provider not in PROVIDERS or provider == "automatic":
        return {}
    return {
        "last_provider": provider,
        "last_provider_at": str(data.get("updated_at", "")),
    }


def record_active_provider(provider: str) -> None:
    """Uloží pouze identifikátor zdroje poslední úspěšné odpovědi."""
    if provider in PROVIDERS and provider != "automatic":
        save_document(ACTIVE_PROVIDER_PATH, {
            "provider": provider,
            "updated_at": datetime.now().isoformat(timespec="seconds"),
        })


def automatic_provider_order() -> list[str]:
    """Vrátí pevné pořadí automatického režimu: Gemini, OpenRouter, lokální."""
    secrets = load_cloud_secrets()
    online = [provider for provider in ("gemini_free", "openrouter_free") if provider in secrets]
    return [*online, "local"]


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


def automatic_provider_request(
    messages: list[dict[str, object]], model: str = "",
) -> tuple[str, str, list[dict[str, str]]]:
    """Přepne Gemini -> OpenRouter -> local pouze po vyčerpání free kvóty."""
    fallbacks: list[dict[str, str]] = []
    for provider in automatic_provider_order():
        started_at = datetime.now()
        try:
            answer = local_model_request(messages, model) if provider == "local" else provider_request(provider, messages, model)
            record_provider_health(provider, True, started_at)
            return provider, answer, fallbacks
        except ProviderQuotaError as error:
            record_provider_health(provider, False, started_at)
            logging.warning("Provider %s vyčerpal free kvótu, zkouším další: %s", provider, error)
            fallbacks.append({"provider": provider, "reason": str(error)[:240]})
        except ValueError:
            record_provider_health(provider, False, started_at)
            raise
    raise ValueError("Automatický režim nemohl získat odpověď z Gemini, OpenRouteru ani lokálního modelu.")


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
        try:
            error_body = error.read().decode("utf-8", errors="replace").lower()
        except OSError:
            error_body = ""
        logging.warning("Online API %s vrátilo %s", provider, error.code)
        quota_markers = ("quota", "resource_exhausted", "rate limit", "rate_limit", "free-models-per-day")
        if error.code in {402, 429} or (error.code == 403 and any(marker in error_body for marker in quota_markers)):
            raise ProviderQuotaError(f"{PROVIDERS[provider]['label']} vyčerpal bezplatný limit.") from error
        if provider == "gemini_free" and error.code == 404:
            raise ValueError("Gemini model není pro tento účet dostupný. Zkontrolujte model nebo klíč.") from error
        raise ValueError(f"Online API vrátilo {error.code}. Ověřte API klíč.") from error
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

    def cors_origin(self) -> str:
        """Povolí pouze současný a předchozí lokální HUD během aktualizace."""
        origin = self.headers.get("Origin", "")
        allowed = {"http://127.0.0.1:5174", "http://127.0.0.1:5173"}
        return origin if origin in allowed else "http://127.0.0.1:5174"

    def send_json(self, payload: dict[str, Any], status: int = 200) -> None:
        raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", self.cors_origin())
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_OPTIONS(self) -> None:
        """Povolí pouze lokální prohlížeč HUD pro ukládání konfigurace."""
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", self.cors_origin())
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
            settings.update(active_provider_status())
            self.send_json(settings)
        elif self.path == "/providers":
            self.send_json(provider_status())
        elif self.path == "/telemetry/settings":
            self.send_json(telemetry_settings_payload())
        elif self.path == "/telemetry/status":
            self.send_json(hardware_status())
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
        elif self.path == "/voice/config":
            config = load_document(VOICE_CONFIG_PATH, "voice")
            self.send_json({"continuous_transcription": bool(config.get("continuous_transcription", False))})
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
                telemetry_features = load_telemetry_settings()["features"]
                if telemetry_features.get("process_termination", True) is not True:
                    raise ValueError("Ukončování procesů je vypnuté v nastavení telemetrie.")
                pid = int(data.get("pid", 0))
                if pid <= 4 or pid == os.getpid():
                    raise ValueError("Tento systémový proces nelze ukončit.")
                if telemetry_features.get("safe_close_before_kill") is True and data.get("force") is not True:
                    try:
                        result = manage_process({"pid": pid, "action": "safe_close"})
                        result["force_available"] = True
                        self.send_json(result)
                        return
                    except ValueError:
                        pass
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
            if self.path == "/processes/manage":
                self.send_json(manage_process(data))
                return
            if self.path == "/telemetry/snapshot":
                if load_telemetry_settings()["features"].get("diagnostic_snapshots") is not True:
                    raise ValueError("Diagnostické snímky jsou vypnuté v telemetrii.")
                self.send_json(save_telemetry_snapshot(str(data.get("label", "snapshot"))))
                return
            if self.path == "/telemetry/export":
                if load_telemetry_settings()["features"].get("diagnostic_export") is not True:
                    raise ValueError("Export telemetrie je vypnutý v nastavení.")
                self.send_json(export_telemetry(str(data.get("format", "json")).lower()))
                return
            if self.path == "/telemetry/compare":
                if load_telemetry_settings()["features"].get("before_after_compare") is not True:
                    raise ValueError("Porovnání před a po je vypnuté v nastavení.")
                self.send_json(telemetry_comparison(str(data.get("phase", "compare"))))
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
            if self.path == "/telemetry/settings":
                self.send_json(update_telemetry_settings(data))
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
                    selected_provider, answer, fallbacks = automatic_provider_request(messages, str(data.get("model", "")))
                else:
                    selected_provider = provider
                    answer = provider_request(selected_provider, messages, str(data.get("model", "")))
                    fallbacks = []
                record_active_provider(selected_provider)
                self.send_json({"provider": selected_provider, "answer": answer, "fallbacks": fallbacks})
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
            if self.path == "/voice/config":
                continuous = data.get("continuous_transcription")
                if not isinstance(continuous, bool):
                    raise ValueError("Trvalý hlasový režim musí mít hodnotu ano nebo ne.")
                config = load_document(VOICE_CONFIG_PATH, "voice")
                config["continuous_transcription"] = continuous
                config["min_command_seconds"] = 0.4
                config["silence_seconds"] = 0.55
                save_document(VOICE_CONFIG_PATH, config)
                state = load_document(VOICE_CONTROL_PATH, "voice")
                state["restart_requested"] = True
                save_document(VOICE_CONTROL_PATH, state)
                logging.info("Trvalý hlasový režim: %s", continuous)
                self.send_json({"continuous_transcription": continuous, "restart_required": True})
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
