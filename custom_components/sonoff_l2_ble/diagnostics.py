"""Native, read-only diagnostics with an explicit non-sensitive export allowlist."""

from __future__ import annotations

import math
from typing import Any

from homeassistant.components import bluetooth
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceEntry

from . import L2ConfigEntry
from .const import (
    CONF_DEVICE_ID,
    CONF_EFFECTS_ENABLED,
    CONF_MTU_WRITES,
    CONF_POLL_INTERVAL,
    CONF_REUSE_CONNECTION,
    DEFAULT_EFFECTS_ENABLED,
    DEFAULT_MTU_WRITES,
    DEFAULT_POLL_INTERVAL,
    DOMAIN,
)
from .protocol import SERVICE
from .state import EFFECTS, integer
from .transport import SessionTiming

READ_OUTCOMES = frozenset(
    {
        "not_attempted",
        "in_progress",
        "reported",
        "auth_failed",
        "connection_failed",
        "invalid_report",
        "cancelled",
    }
)
CONTROL_OUTCOMES = frozenset(
    {
        "not_attempted",
        "in_progress",
        "acknowledged",
        "auth_failed",
        "connection_failed",
        "protocol_error",
        "invalid_request",
        "cancelled",
    }
)
ERROR_TYPES = frozenset(
    {
        "ProtocolError",
        "AuthenticationError",
        "UpdateFailed",
        "ConfigEntryAuthFailed",
        "HomeAssistantError",
        "BleakError",
        "TimeoutError",
        "OSError",
        "ConnectionError",
        "ValueError",
        "TypeError",
        "KeyError",
        "RuntimeError",
    }
)


TIMING_FIELDS = (
    "queue_ms",
    "connect_ms",
    "notify_ms",
    "authentication_ms",
    "command_ms",
    "report_ms",
    "ack_ms",
    "disconnect_ms",
    "total_ms",
    "write_calls",
    "bytes_written",
    "chunk_size",
)


def _number(value: Any) -> int | float | None:
    return (
        value
        if type(value) in (int, float) and 0 <= value <= 1e12 and math.isfinite(value)
        else None
    )


def _transport_timings(transport: Any) -> dict[str, Any]:
    records = getattr(transport, "timings", None)
    if not isinstance(records, dict):
        return {}
    result: dict[str, dict[str, Any]] = {}
    for operation in ("auth", "read", "update", "query", "lease_cleanup"):
        record = records.get(operation)
        if not isinstance(record, SessionTiming):
            continue
        result[operation] = {field: _number(getattr(record, field)) for field in TIMING_FIELDS}
        for field in ("reused_connection", "retained_connection"):
            value = getattr(record, field)
            result[operation][field] = value if type(value) is bool else None
        result[operation]["outcome"] = (
            record.outcome
            if record.outcome in ("in_progress", "success", "auth_failed", "failed", "cancelled")
            else "unknown"
        )
        result[operation]["disconnect_outcome"] = (
            record.disconnect_outcome
            if record.disconnect_outcome
            in ("not_attempted", "in_progress", "success", "failed", "cancelled", "deferred")
            else "unknown"
        )
    return result


def _rssi(value: Any) -> int | None:
    return value if integer(value, -127, 20) else None


def _bluetooth_snapshot(hass: HomeAssistant, entry: L2ConfigEntry) -> dict[str, Any]:
    """Look at HA's cache only; never dump scanner objects or raw advertisements."""
    address = entry.data.get("address")
    if not isinstance(address, str) or not address:
        return {"status": "no_address"}
    try:
        total_scanners = bluetooth.async_scanner_count(hass, connectable=False)
        connectable_scanners = bluetooth.async_scanner_count(hass, connectable=True)
        visible = bluetooth.async_address_present(hass, address, connectable=False)
        connectable = bluetooth.async_address_present(hass, address, connectable=True)
        info = bluetooth.async_last_service_info(
            hass, address, connectable=True
        ) or bluetooth.async_last_service_info(hass, address, connectable=False)
        paths = bluetooth.async_scanner_devices_by_address(hass, address, connectable=True)
    except KeyError, RuntimeError:
        # Diagnostics must still download during setup failure or shutdown.
        return {"status": "not_ready"}
    matches: bool | None = None
    if info and info.name.lower().startswith("ck_"):
        matches = info.name.lower() == f"ck_{entry.data.get(CONF_DEVICE_ID, '')}".lower()
    return {
        "status": "ready",
        "scanner_count": total_scanners,
        "connectable_scanner_count": connectable_scanners,
        "visible": visible,
        "connectable": connectable,
        "matching_device_identity": matches,
        "rssi_dbm": _rssi(info.rssi) if info else None,
        "control_service_advertised": SERVICE in {uuid.lower() for uuid in info.service_uuids}
        if info
        else None,
        "connectable_path_count": len(paths),
        # Export anonymous signal values, not source/adapter MACs, names or hosts.
        "connectable_path_rssi_dbm": sorted(
            value for path in paths if (value := _rssi(path.advertisement.rssi)) is not None
        ),
    }


def _state_presence(data: dict[str, Any]) -> dict[str, bool]:
    """Describe which state fields are known, without exporting household state."""
    mode = data.get("mode")
    level = data.get(f"bright{mode:02}") if mode in EFFECTS.values() else data.get("bright")
    return {
        "power": data.get("switch") in ("on", "off"),
        "mode": integer(mode, 0, 255),
        "brightness": mode != 4 and integer(level, 0, 100),
        "rgb": mode in (0, 1)
        and all(integer(data.get(key), 0, 255) for key in ("colorR", "colorG", "colorB")),
    }


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: L2ConfigEntry
) -> dict[str, Any]:
    """Exclude config data, credentials, identifiers, raw reports and error text.

    HA supplies its standard system/integration version envelope. Both native
    entry/device downloads use the same payload: there is one strip per entry.
    """
    coordinator = getattr(entry, "runtime_data", None)
    runtime = None
    if coordinator is not None:
        read_outcome = (
            coordinator.last_read_outcome
            if coordinator.last_read_outcome in READ_OUTCOMES
            else "unknown"
        )
        control_outcome = (
            coordinator.last_control_outcome
            if coordinator.last_control_outcome in CONTROL_OUTCOMES
            else "unknown"
        )
        fields = _state_presence(coordinator.data)
        error_type = (
            type(coordinator.last_exception).__name__ if coordinator.last_exception else None
        )
        runtime = {
            "available": coordinator.available,
            "reachable": coordinator.reachable,
            "auth_failed": coordinator.auth_failed,
            "state_source": "unknown"
            if not fields["power"]
            else ("reported" if coordinator.reported else "assumed"),
            "known_state_fields": fields,
            "last_update_success": coordinator.last_update_success,
            "last_read_outcome": read_outcome,
            "last_read_success": None
            if read_outcome in ("not_attempted", "in_progress", "unknown")
            else read_outcome == "reported",
            "last_control_outcome": control_outcome,
            "last_error_type": error_type
            if error_type is None or error_type in ERROR_TYPES
            else "OtherError",
            "operation_in_progress": coordinator.operation_lock.locked(),
            "polling_enabled": coordinator.update_interval is not None,
            "last_read_queue_ms": _number(coordinator.last_read_queue_ms),
            "last_control_queue_ms": _number(coordinator.last_control_queue_ms),
            "transport_timings": _transport_timings(coordinator.transport),
            "connection_retained": getattr(coordinator.transport, "connection_retained", False)
            is True,
        }
    return {
        "diagnostics_schema": 4,
        "integration": DOMAIN,
        "options": {
            CONF_EFFECTS_ENABLED: bool(
                entry.options.get(CONF_EFFECTS_ENABLED, DEFAULT_EFFECTS_ENABLED)
            ),
            CONF_POLL_INTERVAL: int(entry.options.get(CONF_POLL_INTERVAL, DEFAULT_POLL_INTERVAL)),
            CONF_MTU_WRITES: bool(entry.options.get(CONF_MTU_WRITES, DEFAULT_MTU_WRITES)),
            CONF_REUSE_CONNECTION: bool(entry.options.get(CONF_REUSE_CONNECTION, False)),
        },
        "bluetooth": _bluetooth_snapshot(hass, entry),
        "runtime": runtime,
    }


async def async_get_device_diagnostics(
    hass: HomeAssistant, entry: L2ConfigEntry, device: DeviceEntry
) -> dict[str, Any]:
    """A single strip belongs to this entry; export no device registry details."""
    return await async_get_config_entry_diagnostics(hass, entry)
