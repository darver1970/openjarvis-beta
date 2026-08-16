"""Lokální sběr hardwarové telemetrie pro HUD JARVIS.

Čte výhradně localhost rozhraní Libre Hardware Monitor a ukládá JSON pro HUD.
Pokud senzor není k dispozici, neodhadujeme hodnoty – zobrazí se jako N/A.
"""

import json
import logging
import os
import subprocess
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path("A:/projekty/OpenJarvis")
HUD_STATUS = ROOT / "hud" / "hardware-status.json"
LOG_PATH = ROOT / "runtime" / "hardware-monitor.log"
SENSOR_URL = "http://127.0.0.1:8085/data.json"
POLL_SECONDS = 2
PROCESS_CPU_SAMPLES: dict[int, tuple[float, float]] = {}

logging.basicConfig(
    filename=LOG_PATH,
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    encoding="utf-8",
)


def write_status(payload: dict[str, Any]) -> None:
    """Zapíše stav atomicky, aby HUD nikdy nečetl neúplný JSON."""
    temporary = HUD_STATUS.with_suffix(".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    os.replace(temporary, HUD_STATUS)


def number(value: Any) -> float | None:
    """Převede text senzoru typu '58.4 °C' na číslo."""
    if value is None:
        return None
    text = str(value).replace(",", ".")
    token = ""
    for char in text:
        if char.isdigit() or char in ".-":
            token += char
        elif token:
            break
    try:
        return float(token)
    except ValueError:
        return None


def walk(node: dict[str, Any], hardware: str = "") -> list[dict[str, Any]]:
    """Zploští strom JSON poskytovaný LHM do seznamu senzorů."""
    current_hardware = str(node.get("Text") or hardware) if node.get("HardwareId") else hardware
    value = node.get("Value")
    sensor_type = str(node.get("Type") or node.get("SensorType") or "")
    items: list[dict[str, Any]] = []
    if value not in (None, ""):
        items.append({
            "hardware": hardware,
            "name": str(node.get("Text") or "Senzor"),
            "type": sensor_type.lower(),
            "value": number(value),
            "unit": str(value),
        })
    for child in node.get("Children") or []:
        if isinstance(child, dict):
            items.extend(walk(child, current_hardware))
    return items


def fetch_sensors() -> list[dict[str, Any]]:
    """Načte senzory pouze z lokálního LHM serveru."""
    request = urllib.request.Request(SENSOR_URL, headers={"User-Agent": "JarvisLocalHUD/1"})
    with urllib.request.urlopen(request, timeout=2) as response:
        data = json.loads(response.read().decode("utf-8"))
    sensors: list[dict[str, Any]] = []
    for root in data.get("Children") or []:
        if isinstance(root, dict):
            sensors.extend(walk(root))
    return sensors


def choose(sensors: list[dict[str, Any]], words: tuple[str, ...], kinds: tuple[str, ...]) -> dict[str, Any] | None:
    """Vybere první reálně hlášený senzor podle názvu a typu."""
    for sensor in sensors:
        label = f"{sensor['hardware']} {sensor['name']}".lower()
        if sensor["value"] is not None and any(word in label for word in words) and sensor["type"] in kinds:
            return sensor
    return None


def native_disks() -> list[dict[str, Any]]:
    """Vrátí obsazení lokálních pevných disků přes PowerShell."""
    script = "Get-CimInstance Win32_LogicalDisk -Filter \"DriveType=3\" | Select-Object DeviceID,Size,FreeSpace | ConvertTo-Json -Compress"
    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-Command", script],
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=5,
            check=True,
        )
        drives = json.loads(result.stdout or "[]")
        if isinstance(drives, dict):
            drives = [drives]
        return [
            {
                "name": drive.get("DeviceID", "Disk"),
                "used": round(100 * (1 - int(drive.get("FreeSpace", 0)) / int(drive.get("Size", 1)))),
                "free_gb": round(int(drive.get("FreeSpace", 0)) / 1073741824, 1),
            }
            for drive in drives if int(drive.get("Size", 0)) > 0
        ]
    except (subprocess.SubprocessError, json.JSONDecodeError, OSError) as error:
        logging.warning("Disková telemetrie selhala: %s", error)
        return []


def native_processes() -> list[dict[str, Any]]:
    """Vrátí největší lokální procesy bez možnosti je měnit nebo ukončit."""
    script = (
        "Get-Process | Sort-Object WorkingSet64 -Descending | Select-Object -First 45 "
        "ProcessName,Id,CPU,WorkingSet64,Responding,Handles,@{n='ThreadCount';e={$_.Threads.Count}} | ConvertTo-Json -Compress"
    )
    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-Command", script], capture_output=True,
            text=True, encoding="utf-8", timeout=7, check=True,
        )
        processes = json.loads(result.stdout or "[]")
        if isinstance(processes, dict):
            processes = [processes]
        now = time.monotonic()
        logical_processors = max(os.cpu_count() or 1, 1)
        active_pids: set[int] = set()
        output: list[dict[str, Any]] = []
        for item in processes:
            pid = int(item.get("Id") or 0)
            cpu_seconds = float(item.get("CPU") or 0)
            previous = PROCESS_CPU_SAMPLES.get(pid)
            cpu_percent = 0.0
            if previous:
                elapsed = now - previous[1]
                if elapsed > 0:
                    cpu_percent = round(max(0, (cpu_seconds - previous[0]) / elapsed / logical_processors * 100), 1)
            PROCESS_CPU_SAMPLES[pid] = (cpu_seconds, now)
            active_pids.add(pid)
            output.append({
                "name": item.get("ProcessName", "proces"), "pid": pid,
                "cpu_percent": cpu_percent,
                "memory_mb": round(int(item.get("WorkingSet64") or 0) / 1048576, 1),
                "status": "BĚŽÍ" if bool(item.get("Responding", True)) else "NEODPOVÍDÁ",
                "handles": int(item.get("Handles") or 0),
                "threads": int(item.get("ThreadCount") or 0),
            })
        for pid in tuple(PROCESS_CPU_SAMPLES):
            if pid not in active_pids:
                PROCESS_CPU_SAMPLES.pop(pid, None)
        return sorted(output, key=lambda process: (process["cpu_percent"], process["memory_mb"]), reverse=True)
    except (subprocess.SubprocessError, json.JSONDecodeError, OSError) as error:
        logging.warning("Seznam procesů nelze načíst: %s", error)
        return []


def sensor_value(sensors: list[dict[str, Any]], words: tuple[str, ...], kinds: tuple[str, ...]) -> float | None:
    """Vrátí numerickou hodnotu senzoru, pokud ji hardware skutečně hlásí."""
    item = choose(sensors, words, kinds)
    return item["value"] if item else None


def sensor_value_all(sensors: list[dict[str, Any]], words: tuple[str, ...], kinds: tuple[str, ...]) -> float | None:
    """Najde senzor, který obsahuje všechny požadované části názvu."""
    for sensor in sensors:
        label = f"{sensor['hardware']} {sensor['name']}".lower()
        if sensor["type"] in kinds and sensor["value"] is not None and all(word in label for word in words):
            return sensor["value"]
    return None


def snapshot() -> dict[str, Any]:
    """Sestaví bezpečný snímek bez smyšlených teplot."""
    sensors = fetch_sensors()
    cpu_temp = choose(sensors, ("cpu", "package", "tdie", "core"), ("temperature",))
    gpu_temp = choose(sensors, ("gpu", "radeon", "nvidia", "geforce"), ("temperature",))
    cpu_load = choose(sensors, ("cpu", "total"), ("load",))
    gpu_load = choose(sensors, ("gpu", "radeon", "nvidia", "geforce"), ("load",))
    ram_load = choose(sensors, ("memory", "ram"), ("load",))
    temperatures = [sensor for sensor in sensors if sensor["type"] == "temperature" and sensor["value"] is not None]
    performance = {
        "cpu_clock_mhz": sensor_value(sensors, ("cpu", "cores"), ("clock",)),
        "cpu_power_w": sensor_value(sensors, ("cpu", "package"), ("power",)),
        "gpu_clock_mhz": sensor_value(sensors, ("gpu", "radeon", "nvidia"), ("clock",)),
        "gpu_power_w": sensor_value(sensors, ("gpu", "radeon", "nvidia"), ("power",)),
        "ram_used_gb": sensor_value_all(sensors, ("total memory", "used"), ("data",)),
        "ram_available_gb": sensor_value_all(sensors, ("total memory", "available"), ("data",)),
        "network_load": sensor_value(sensors, ("network utilization",), ("load",)),
        "fan_rpm": sensor_value(sensors, ("cpu", "fan"), ("fan",)),
    }
    return {
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "source": "Libre Hardware Monitor / localhost",
        "online": True,
        "cpu": {"temperature": cpu_temp and cpu_temp["value"], "load": cpu_load and cpu_load["value"]},
        "gpu": {"temperature": gpu_temp and gpu_temp["value"], "load": gpu_load and gpu_load["value"]},
        "ram": {"load": ram_load and ram_load["value"]},
        "performance": performance,
        "temperatures": temperatures[:20],
        "disks": native_disks(),
        "processes": native_processes(),
    }


def main() -> None:
    """Běží jako samostatný lokální proces HUD."""
    logging.info("Spouštím hardware monitor")
    while True:
        try:
            write_status(snapshot())
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError, ValueError) as error:
            logging.warning("LHM není připraven: %s", error)
            write_status({
                "updated_at": datetime.now(timezone.utc).isoformat(),
                "source": "Libre Hardware Monitor / localhost",
                "online": False,
                "message": "ČEKÁM NA SENZORY",
                "cpu": {}, "gpu": {}, "ram": {}, "performance": {}, "temperatures": [],
                "disks": native_disks(), "processes": native_processes(),
            })
        time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    main()
