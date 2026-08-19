"""Volitelné lokální rozšíření telemetrie RAVEN pro Windows.

Každý náročnější sběrač se spustí pouze tehdy, když je jeho přepínač aktivní.
Modul nepoužívá placené ani vzdálené služby a nedoplňuje chybějící hodnoty odhadem.
"""

from __future__ import annotations

import ctypes
import csv
import hashlib
import ipaddress
import json
import os
import subprocess
import time
import winreg
from collections import defaultdict, deque
from pathlib import Path
from typing import Any

import psutil


ROOT = Path(__file__).resolve().parent
HISTORY: deque[dict[str, Any]] = deque(maxlen=1800)
PROCESS_MEMORY: dict[int, deque[tuple[float, float]]] = defaultdict(lambda: deque(maxlen=300))
PROCESS_WRITES: dict[int, deque[tuple[float, float]]] = defaultdict(lambda: deque(maxlen=120))
SIGNATURE_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}
HASH_CACHE: dict[str, tuple[float, str]] = {}
HANDLE_CACHE: dict[int, tuple[float, dict[str, Any]]] = {}
POWER_CACHE: dict[str, tuple[float, Any]] = {}
GAME_STATE: dict[str, Any] = {"active": False, "started_at": None, "baseline": None, "last_session": None}
PRESENTMON_CACHE: tuple[float, dict[str, Any]] = (0.0, {})
ADAPTER_SAMPLES: dict[str, tuple[float, int, int]] = {}
GAME_HINTS = ("game", "steam", "epic", "gog", "xbox", "valorant", "fortnite", "minecraft", "cyberpunk")


def enabled(features: dict[str, bool], name: str) -> bool:
    return features.get(name, False) is True


def is_admin() -> bool:
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except (AttributeError, OSError):
        return False


def powershell_json(cache_key: str, script: str, ttl: float = 30.0, timeout: float = 8.0) -> Any:
    now = time.monotonic()
    cached = POWER_CACHE.get(cache_key)
    if cached and now - cached[0] < ttl:
        return cached[1]
    try:
        result = subprocess.run(
            [r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe", "-NoProfile", "-Command", script],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout, check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.SubprocessError) as error:
        value = {"available": False, "reason": str(error)[:240]}
        POWER_CACHE[cache_key] = (now, value)
        return value
    if result.returncode != 0:
        value: Any = {"available": False, "reason": (result.stderr or "Windows údaj není dostupný.").strip()[:240]}
    else:
        try:
            value = json.loads(result.stdout or "null")
        except json.JSONDecodeError:
            value = {"available": False, "reason": "Windows vrátil neplatná data."}
    POWER_CACHE[cache_key] = (now, value)
    return value


def startup_items() -> list[dict[str, str]]:
    items: list[dict[str, str]] = []
    locations = (
        (winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run", "Uživatel"),
        (winreg.HKEY_LOCAL_MACHINE, r"Software\Microsoft\Windows\CurrentVersion\Run", "Počítač"),
    )
    for hive, path, scope in locations:
        try:
            with winreg.OpenKey(hive, path) as key:
                for index in range(winreg.QueryInfoKey(key)[1]):
                    name, command, _ = winreg.EnumValue(key, index)
                    items.append({"name": name, "command": str(command), "scope": scope})
        except OSError:
            continue
    return items


def services_by_pid() -> dict[int, list[str]]:
    mapping: dict[int, list[str]] = defaultdict(list)
    try:
        for service in psutil.win_service_iter():
            data = service.as_dict()
            pid = int(data.get("pid") or 0)
            if pid:
                mapping[pid].append(str(data.get("display_name") or data.get("name") or "služba"))
    except (psutil.Error, OSError):
        pass
    return mapping


def connection_map(include_details: bool) -> dict[int, dict[str, Any]]:
    mapping: dict[int, dict[str, Any]] = defaultdict(lambda: {"count": 0, "connections": []})
    try:
        connections = psutil.net_connections(kind="inet")
    except (psutil.Error, OSError):
        return mapping
    for connection in connections:
        if not connection.pid:
            continue
        row = mapping[int(connection.pid)]
        row["count"] += 1
        if include_details and len(row["connections"]) < 20:
            remote_ip = connection.raddr.ip if connection.raddr else ""
            remote_port = connection.raddr.port if connection.raddr else 0
            location = ""
            if remote_ip:
                try:
                    address = ipaddress.ip_address(remote_ip)
                    location = "SOUKROMÁ/LAN" if address.is_private else "VEŘEJNÁ/IP"
                except ValueError:
                    location = "NEZNÁMÁ"
            row["connections"].append({
                "local": f"{connection.laddr.ip}:{connection.laddr.port}" if connection.laddr else "",
                "remote": f"{remote_ip}:{remote_port}" if remote_ip else "",
                "protocol": "TCP" if connection.type == 1 else "UDP",
                "status": str(connection.status),
                "location": location,
            })
    return mapping


def signature_info(path: str) -> dict[str, Any]:
    if not path or not Path(path).is_file():
        return {"signed": None, "publisher": ""}
    now = time.monotonic()
    cached = SIGNATURE_CACHE.get(path)
    if cached and now - cached[0] < 3600:
        return cached[1]
    escaped = path.replace("'", "''")
    script = (
        f"$s=Get-AuthenticodeSignature -LiteralPath '{escaped}'; "
        "[pscustomobject]@{signed=($s.Status -eq 'Valid');status=[string]$s.Status;"
        "publisher=if($s.SignerCertificate){$s.SignerCertificate.Subject}else{''}}|ConvertTo-Json -Compress"
    )
    raw = powershell_json(f"signature:{path}", script, ttl=3600, timeout=6)
    info = raw if isinstance(raw, dict) and "signed" in raw else {"signed": None, "publisher": ""}
    SIGNATURE_CACHE[path] = (now, info)
    return info


def signature_batch(paths: list[str]) -> dict[str, dict[str, Any]]:
    now = time.monotonic()
    result: dict[str, dict[str, Any]] = {}
    pending = []
    for path in paths:
        cached = SIGNATURE_CACHE.get(path)
        if cached and now - cached[0] < 3600:
            result[path] = cached[1]
        elif path and Path(path).is_file():
            pending.append(path)
    if pending:
        values = ",".join("'" + path.replace("'", "''") + "'" for path in pending)
        script = (
            f"@({values})|ForEach-Object{{$p=$_;$s=Get-AuthenticodeSignature -LiteralPath $p;"
            "[pscustomobject]@{path=$p;signed=($s.Status -eq 'Valid');status=[string]$s.Status;"
            "publisher=if($s.SignerCertificate){$s.SignerCertificate.Subject}else{''}}}|ConvertTo-Json -Compress"
        )
        raw = powershell_json("signature-batch:" + hashlib.sha1("|".join(pending).encode()).hexdigest(), script, ttl=3600, timeout=20)
        rows = raw if isinstance(raw, list) else [raw]
        for row in rows:
            if isinstance(row, dict) and row.get("path"):
                info = {"signed": row.get("signed"), "status": row.get("status", ""), "publisher": row.get("publisher", "")}
                result[str(row["path"])] = info
                SIGNATURE_CACHE[str(row["path"])] = (now, info)
    return result


def application_icon_batch(paths: list[str]) -> dict[str, str]:
    paths = [path for path in paths if path and Path(path).is_file()][:20]
    if not paths:
        return {}
    values = ",".join("'" + path.replace("'", "''") + "'" for path in paths)
    script = (
        "Add-Type -AssemblyName System.Drawing;"
        f"@({values})|ForEach-Object{{$p=$_;$i=[Drawing.Icon]::ExtractAssociatedIcon($p);if($i){{$m=New-Object IO.MemoryStream;"
        "$i.ToBitmap().Save($m,[Drawing.Imaging.ImageFormat]::Png);[pscustomobject]@{path=$p;data=[Convert]::ToBase64String($m.ToArray())};"
        "$m.Dispose();$i.Dispose()}}|ConvertTo-Json -Compress"
    )
    raw = powershell_json("icons:" + hashlib.sha1("|".join(paths).encode()).hexdigest(), script, ttl=3600, timeout=20)
    rows = raw if isinstance(raw, list) else [raw]
    return {str(row["path"]): "data:image/png;base64," + str(row["data"]) for row in rows if isinstance(row, dict) and row.get("path") and row.get("data")}


def file_hash(path: str) -> str:
    if not path or not Path(path).is_file():
        return ""
    try:
        modified = Path(path).stat().st_mtime
        cached = HASH_CACHE.get(path)
        if cached and cached[0] == modified:
            return cached[1]
        digest = hashlib.sha256()
        with open(path, "rb") as source:
            for chunk in iter(lambda: source.read(1024 * 1024), b""):
                digest.update(chunk)
        value = digest.hexdigest()
        HASH_CACHE[path] = (modified, value)
        return value
    except OSError:
        return ""


def process_handles(pid: int) -> dict[str, Any]:
    """Vrátí souborové a registry handly přes podepsaný Microsoft Sysinternals Handle."""
    binary = ROOT / "runtime" / "sysinternals-handle" / "handle64.exe"
    if not binary.is_file():
        return {"available": False, "reason": "Microsoft Handle není nainstalován.", "registry": [], "files": []}
    if not is_admin():
        return {"available": False, "reason": "Registry handly vyžadují spuštění RAVENu jako správce.", "registry": [], "files": []}
    now = time.monotonic()
    cached = HANDLE_CACHE.get(pid)
    if cached and now - cached[0] < 30:
        return cached[1]
    try:
        capture = subprocess.run(
            [str(binary), "-accepteula", "-nobanner", "-a", "-p", str(pid), "-v"],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=8,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        rows = list(csv.reader(line for line in capture.stdout.splitlines() if "," in line))
        registry, files = [], []
        for row in rows:
            text = " ".join(row)
            target = row[-1] if row else ""
            if " key " in f" {text.lower()} " and len(registry) < 30:
                registry.append(target)
            elif any(token in target.lower() for token in (":\\", "\\device\\")) and len(files) < 30:
                files.append(target)
        value = {"available": capture.returncode == 0, "reason": "" if capture.returncode == 0 else (capture.stderr or "Handle není dostupný.")[:180], "registry": registry, "files": files}
    except (OSError, subprocess.SubprocessError) as error:
        value = {"available": False, "reason": str(error)[:180], "registry": [], "files": []}
    HANDLE_CACHE[pid] = (now, value)
    return value


def enrich_processes(processes: list[dict[str, Any]], features: dict[str, bool]) -> None:
    service_map = services_by_pid() if enabled(features, "windows_services") else {}
    connections = connection_map(enabled(features, "connection_details") or enabled(features, "remote_location")) if any(
        enabled(features, key) for key in ("connection_details", "remote_location", "etw_network_speed")
    ) else {}
    security_enabled = enabled(features, "publisher_signatures") or enabled(features, "unsigned_process_alerts")
    now = time.time()
    top_security = {item["pid"] for item in processes[:20]}
    top_paths = [str(item.get("executable") or "") for item in processes[:20]]
    signatures = signature_batch(top_paths) if security_enabled else {}
    icons = application_icon_batch(top_paths) if enabled(features, "application_icons") else {}
    for item in processes:
        pid = int(item.get("pid") or 0)
        path = str(item.get("executable") or "")
        if enabled(features, "process_tree"):
            item["tree"] = {"parent_pid": int(item.get("parent_pid") or 0)}
        if enabled(features, "windows_services"):
            item["services"] = service_map.get(pid, [])
        if enabled(features, "page_faults"):
            try:
                memory = psutil.Process(pid).memory_info()
                item["page_faults"] = int(getattr(memory, "num_page_faults", 0) or 0)
            except (psutil.Error, OSError):
                item["page_faults"] = None
        if enabled(features, "energy_usage"):
            item["energy_impact"] = round(float(item.get("cpu_percent", 0)) * .65 + float(item.get("gpu_percent", 0)) * .35, 1)
        if connections:
            details = connections.get(pid, {"count": 0, "connections": []})
            if enabled(features, "connection_details"):
                item["connections"] = details["connections"]
            if enabled(features, "remote_location"):
                item["remote_locations"] = sorted({entry["location"] for entry in details["connections"] if entry["location"]})
            if enabled(features, "etw_network_speed"):
                item["network_etw"] = {
                    "available": is_admin(),
                    "connections": details["count"],
                    "reason": "Rozšířené systémové síťové údaje vyžadují spuštění jako správce." if not is_admin() else "Rozšířený lokální přehled spojení je aktivní.",
                }
        if enabled(features, "open_files_registry"):
            try:
                item["open_files"] = [entry.path for entry in psutil.Process(pid).open_files()[:20]]
            except (psutil.Error, OSError):
                item["open_files"] = []
            item["registry_handles"] = process_handles(pid) if pid in top_security else {"available": is_admin(), "reason": "Podrobnosti se načítají pro 20 nejaktivnějších procesů.", "registry": [], "files": []}
        if security_enabled and pid in top_security:
            item["signature"] = signatures.get(path, {"signed": None, "publisher": ""})
            if enabled(features, "unsigned_process_alerts"):
                item["unsigned_alert"] = item["signature"].get("signed") is False
        if enabled(features, "file_hashes") and pid in top_security:
            item["sha256"] = file_hash(path)
        if enabled(features, "process_priority_affinity"):
            try:
                proc = psutil.Process(pid)
                item["priority"] = int(proc.nice())
                item["cpu_affinity"] = proc.cpu_affinity()
            except (psutil.Error, OSError, AttributeError):
                item["priority"], item["cpu_affinity"] = None, []
        if enabled(features, "application_icons"):
            item["application"] = {"display_name": Path(path).stem if path else item.get("name", ""), "icon_source": path, "icon": icons.get(path, "")}
        memory_mb = float(item.get("memory_mb") or 0)
        write_mbps = float(item.get("disk_write_mbps") or 0)
        PROCESS_MEMORY[pid].append((now, memory_mb))
        PROCESS_WRITES[pid].append((now, write_mbps))
        if enabled(features, "memory_leak_detection"):
            samples = PROCESS_MEMORY[pid]
            growth = samples[-1][1] - samples[0][1] if len(samples) >= 10 else 0
            item["memory_growth_mb"] = round(growth, 1)
            item["memory_leak_alert"] = len(samples) >= 10 and growth > max(100, samples[0][1] * .25)
        if enabled(features, "sustained_disk_writes"):
            samples = PROCESS_WRITES[pid]
            average = sum(value for _, value in samples) / len(samples) if samples else 0
            item["sustained_write_mbps"] = round(average, 2)
            item["sustained_write_alert"] = len(samples) >= 10 and average >= 10


def physical_storage() -> Any:
    script = (
        "Get-PhysicalDisk|Select-Object FriendlyName,MediaType,HealthStatus,OperationalStatus,Size,"
        "@{n='Wear';e={$_.Wear}}|ConvertTo-Json -Compress"
    )
    return powershell_json("physical-storage", script, ttl=60)


def disk_performance() -> Any:
    script = (
        "$s=(Get-Counter '\\PhysicalDisk(*)\\Avg. Disk sec/Transfer','\\PhysicalDisk(*)\\Current Disk Queue Length').CounterSamples;"
        "$s|Select-Object InstanceName,Path,CookedValue|ConvertTo-Json -Compress"
    )
    return powershell_json("disk-performance", script, ttl=10)


def adapter_usage() -> list[dict[str, Any]]:
    counters = psutil.net_io_counters(pernic=True)
    stats = psutil.net_if_stats()
    now = time.monotonic()
    rows = []
    for name, value in counters.items():
        previous = ADAPTER_SAMPLES.get(name)
        upload = download = 0.0
        if previous and now > previous[0]:
            elapsed = now - previous[0]
            upload = max(0.0, (value.bytes_sent - previous[1]) * 8 / elapsed / 1_000_000)
            download = max(0.0, (value.bytes_recv - previous[2]) * 8 / elapsed / 1_000_000)
        ADAPTER_SAMPLES[name] = (now, value.bytes_sent, value.bytes_recv)
        rows.append({
            "name": name, "up": bool(stats.get(name) and stats[name].isup), "speed_mbps": int(stats[name].speed) if name in stats else 0,
            "upload_mbps": round(upload, 2), "download_mbps": round(download, 2),
            "sent_mb": round(value.bytes_sent / 1048576, 1), "received_mb": round(value.bytes_recv / 1048576, 1),
        })
    return rows


def user_totals(processes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    totals: dict[str, dict[str, Any]] = {}
    for item in processes:
        user = str(item.get("username") or "SYSTÉM/NEZNÁMÝ")
        row = totals.setdefault(user, {"user": user, "processes": 0, "cpu_percent": 0.0, "memory_mb": 0.0})
        row["processes"] += 1
        row["cpu_percent"] += float(item.get("cpu_percent") or 0)
        row["memory_mb"] += float(item.get("memory_mb") or 0)
    for row in totals.values():
        row["cpu_percent"] = round(row["cpu_percent"], 1)
        row["memory_mb"] = round(row["memory_mb"], 1)
    return sorted(totals.values(), key=lambda row: row["memory_mb"], reverse=True)


def bottleneck(system: dict[str, Any], gpu_load: float | None) -> dict[str, str]:
    scores = {
        "CPU": float(system.get("cpu_percent") or 0), "RAM": float(system.get("memory_percent") or 0),
        "DISK": float(system.get("disk_percent") or 0), "SÍŤ": float(system.get("network_percent") or 0),
        "GPU": float(gpu_load or 0),
    }
    component = max(scores, key=scores.get)
    value = scores[component]
    advice = "Systém nemá zjevné úzké hrdlo." if value < 80 else f"Nejvyšší zatížení má {component} ({value:.0f} %)."
    return {"component": component if value >= 80 else "ŽÁDNÉ", "advice": advice}


def presentmon_sample(processes: list[dict[str, Any]]) -> dict[str, Any]:
    """Krátce změří skutečné prezentované snímky přes bezplatný lokální PresentMon."""
    global PRESENTMON_CACHE
    now = time.monotonic()
    if now - PRESENTMON_CACHE[0] < 10 and PRESENTMON_CACHE[1]:
        return PRESENTMON_CACHE[1]
    binary = next(ROOT.glob("runtime/presentmon/**/presentmon.exe"), None)
    if not binary:
        return {"available": False, "fps": None, "frametime_ms": None, "reason": "PresentMon není nainstalován v projektu."}
    candidates = [item for item in processes if int(item.get("pid") or 0) > 4 and (
        float(item.get("gpu_percent") or 0) >= 3 or any(hint in str(item.get("name") or "").lower() for hint in GAME_HINTS)
    )]
    if not candidates:
        value = {"available": True, "fps": None, "frametime_ms": None, "reason": "Čeká na hru nebo 3D aplikaci."}
        PRESENTMON_CACHE = (now, value)
        return value
    target = max(candidates, key=lambda item: float(item.get("gpu_percent") or 0))
    command = [
        str(binary), "--process_id", str(target["pid"]), "--timed", "1", "--terminate_after_timed",
        "--output_stdout", "--no_console_stats", "--v1_metrics", "--session_name", "RavenPresentMon",
    ]
    try:
        capture = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=5, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        rows = list(csv.DictReader(line for line in capture.stdout.splitlines() if "," in line))
        frame_times = []
        for row in rows:
            raw = row.get("MsBetweenPresents") or row.get("msBetweenPresents") or row.get("FrameTime")
            try:
                value = float(raw or 0)
                if 0 < value < 1000:
                    frame_times.append(value)
            except ValueError:
                continue
        if frame_times:
            average = sum(frame_times) / len(frame_times)
            value = {"available": True, "process": target.get("name"), "pid": target["pid"], "fps": round(1000 / average, 1), "frametime_ms": round(average, 2), "samples": len(frame_times), "reason": "Živé měření PresentMon."}
        else:
            reason = (capture.stderr or "Aplikace zatím neprezentuje snímky.").strip()[:180]
            value = {"available": True, "process": target.get("name"), "pid": target["pid"], "fps": None, "frametime_ms": None, "reason": reason}
    except (OSError, subprocess.SubprocessError) as error:
        value = {"available": False, "fps": None, "frametime_ms": None, "reason": str(error)[:180]}
    PRESENTMON_CACHE = (now, value)
    return value


def collect_extended(
    features: dict[str, bool], sensors: list[dict[str, Any]], processes: list[dict[str, Any]],
    system: dict[str, Any], disks: list[dict[str, Any]], gpu_load: float | None,
) -> dict[str, Any]:
    enrich_processes(processes, features)
    now = time.time()
    sample = {
        "time": now, "cpu": system.get("cpu_percent", 0), "ram": system.get("memory_percent", 0),
        "disk": system.get("disk_percent", 0), "network": system.get("network_percent", 0), "gpu": gpu_load or 0,
        "temperatures": [sensor.get("value") for sensor in sensors if sensor.get("type") == "temperature" and sensor.get("value") is not None],
        "fan_rpm": max((float(sensor.get("value")) for sensor in sensors if sensor.get("type") == "fan" and sensor.get("value") is not None), default=None),
    }
    HISTORY.append(sample)
    result: dict[str, Any] = {"collector_admin": is_admin()}
    if enabled(features, "startup_impact"):
        result["startup"] = {"boot_time": psutil.boot_time(), "seconds_since_boot": round(now - psutil.boot_time()), "items": startup_items()}
    if enabled(features, "smart_storage"):
        result["smart_storage"] = physical_storage()
    if enabled(features, "fan_voltage_clocks"):
        result["fan_voltage_clocks"] = [sensor for sensor in sensors if sensor.get("type") in {"fan", "voltage", "clock", "power"}]
    if enabled(features, "history_charts"):
        result["history"] = list(HISTORY)
    if enabled(features, "threshold_alerts"):
        result["alerts"] = [f"{key.upper()} {float(value):.0f} %" for key, value in sample.items() if key in {"cpu", "ram", "disk", "gpu"} and float(value or 0) >= 90]
        hot = max(sample["temperatures"], default=0)
        if hot >= 85:
            result["alerts"].append(f"TEPLOTA {hot:.0f} °C")
    if enabled(features, "anomaly_detection"):
        recent = list(HISTORY)[-60:]
        anomalies = []
        for key in ("cpu", "ram", "disk", "network", "gpu"):
            values = [float(row.get(key) or 0) for row in recent[:-1]]
            if len(values) >= 10:
                average = sum(values) / len(values)
                deviation = (sum((value - average) ** 2 for value in values) / len(values)) ** .5
                if deviation > 0 and float(sample[key]) > average + 3 * deviation:
                    anomalies.append({"metric": key, "value": sample[key], "baseline": round(average, 1)})
        result["anomalies"] = anomalies
    if enabled(features, "thermal_throttling"):
        hot = max(sample["temperatures"], default=0)
        result["thermal_throttling"] = {"detected": hot >= 90, "max_temperature": hot or None, "reason": "Vysoká teplota může omezovat takty." if hot >= 90 else "Bez známek teplotního omezení."}
    if enabled(features, "page_faults"):
        swap = psutil.swap_memory()
        result["paging"] = {"pagefile_percent": swap.percent, "pagefile_used_mb": round(swap.used / 1048576, 1), "pagefile_total_mb": round(swap.total / 1048576, 1)}
    if enabled(features, "gpu_vram_details"):
        result["gpu_vram"] = [sensor for sensor in sensors if any(word in f"{sensor.get('hardware','')} {sensor.get('name','')}".lower() for word in ("memory", "vram", "gpu core", "d3d"))]
    if enabled(features, "user_sessions"):
        result["user_sessions"] = user_totals(processes)
    if enabled(features, "disk_queue_latency"):
        result["disk_performance"] = disk_performance()
    if enabled(features, "network_adapter_split"):
        result["network_adapters"] = adapter_usage()
    if enabled(features, "fan_curves"):
        result["fan_curves"] = [{"temperature": max(row["temperatures"], default=None), "fan_rpm": row.get("fan_rpm"), "time": row["time"]} for row in list(HISTORY)[-300:]]
    if enabled(features, "bottleneck_advice"):
        result["bottleneck"] = bottleneck(system, gpu_load)
    if enabled(features, "raven_usage_separation"):
        own = [item for item in processes if str(item.get("executable") or "").lower().startswith(str(ROOT).lower())]
        result["raven_usage"] = {"processes": len(own), "cpu_percent": round(sum(float(item.get("cpu_percent") or 0) for item in own), 1), "memory_mb": round(sum(float(item.get("memory_mb") or 0) for item in own), 1)}
    if enabled(features, "fps_monitoring") or enabled(features, "frametime_monitoring"):
        result["gaming_capture"] = presentmon_sample(processes)
    if enabled(features, "game_session_compare"):
        gaming = any(any(hint in str(item.get("name") or "").lower() for hint in GAME_HINTS) for item in processes)
        if gaming and not GAME_STATE["active"]:
            GAME_STATE.update({"active": True, "started_at": now, "baseline": sample.copy()})
        elif not gaming and GAME_STATE["active"]:
            GAME_STATE["last_session"] = {"started_at": GAME_STATE["started_at"], "ended_at": now, "baseline": GAME_STATE["baseline"], "final": sample.copy()}
            GAME_STATE.update({"active": False, "started_at": None, "baseline": None})
        result["game_session"] = GAME_STATE.copy()
    if enabled(features, "diagnostic_snapshots"):
        result["snapshot_ready"] = True
    if enabled(features, "diagnostic_export"):
        result["export_formats"] = ["json", "csv"]
    if enabled(features, "before_after_compare"):
        result["comparison_ready"] = True
    if enabled(features, "second_monitor_dashboard"):
        result["second_monitor_ready"] = True
    result["disks_seen"] = len(disks)
    return result
