"""Export one existing SonoffLAN cached device to a new owner-only local file.

Never log credentials, modify HA storage, or perform an eWeLink cloud login.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any


def extract(config_dir: Path, device_id: str, output: Path) -> None:
    """Read the selected cache record and exclusively create the secret output."""
    candidates: list[dict[str, Any]] = []
    for path in sorted((config_dir / ".storage" / "sonoff").glob("*.json")):
        try:
            content = json.loads(path.read_text())
        except OSError, ValueError:
            continue
        devices = content.get("data", []) if isinstance(content, dict) else []
        if isinstance(devices, list):
            candidates.extend(
                device
                for device in devices
                if isinstance(device, dict) and device.get("deviceid") == device_id
            )
    keys = {device.get("apikey") for device in candidates if isinstance(device.get("apikey"), str)}
    if len(keys) != 1:
        raise ValueError("No unique cached apikey found for this device ID")
    key = keys.pop()
    if not key:
        raise ValueError("The cached apikey is empty")
    data = {
        "device_id": device_id,
        "device_apikey": key,
        "account_apikey": key,
        "account_key_note": "Owner-key default; shared devices may require a separate account apikey.",
    }
    fd = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as stream:
        json.dump(data, stream, indent=2)
        stream.write("\n")


def main() -> None:
    """Require an explicit device and output path; print only a completion notice."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config-dir", type=Path, required=True)
    parser.add_argument("--device-id", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        extract(args.config_dir, args.device_id, args.output)
    except OSError, ValueError:
        parser.exit(
            1,
            "Export failed. Check the config path, device ID and cache; output must not already exist.\n",
        )
    print("Credentials exported to the requested owner-only file. No values were printed.")


if __name__ == "__main__":
    main()
