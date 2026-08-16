"""Pasivní lokální monitor síťových spojení pro J.A.R.V.I.S."""

from __future__ import annotations

import json
import logging
import subprocess
import time
from datetime import datetime
from pathlib import Path


ROOT = Path("A:/projekty/OpenJarvis")
CONFIG_PATH = ROOT / "runtime" / "network-defense.json"
STATUS_PATH = ROOT / "hud" / "network-status.json"
LOG_PATH = ROOT / "runtime" / "network-monitor.log"


def publish(status: dict[str, object]) -> None:
    """Zapíše stav atomicky pro lokální HUD."""
    temporary = STATUS_PATH.with_suffix(".tmp")
    temporary.write_text(json.dumps(status, ensure_ascii=False), encoding="utf-8")
    temporary.replace(STATUS_PATH)


def connection_count() -> int:
    """Vrátí počet aktivních TCP spojení vlastního počítače."""
    result = subprocess.run(
        ["netstat", "-ano", "-p", "tcp"],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    return sum(1 for line in result.stdout.splitlines() if "ESTABLISHED" in line)


def main() -> None:
    """Průběžně sleduje pouze lokální počítač bez síťového skenování."""
    config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    interval = int(config["interval_seconds"])
    threshold = int(config["connection_alert_threshold"])
    logging.basicConfig(filename=LOG_PATH, level=logging.INFO, encoding="utf-8")
    while True:
        count = connection_count()
        risk = "NÍZKÉ" if count < threshold else "ZVÝŠENÉ"
        status = {
            "updated": datetime.now().isoformat(),
            "connections": count,
            "risk": risk,
            "auto_block": False,
            "message": "Pasivní monitoring aktivní" if risk == "NÍZKÉ" else "Neobvykle mnoho aktivních spojení",
        }
        publish(status)
        logging.info("Spojení: %s; riziko: %s", count, risk)
        time.sleep(interval)


if __name__ == "__main__":
    main()
