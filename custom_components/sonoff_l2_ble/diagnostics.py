"""Credential-safe diagnostics: whitelist operational flags, never dump entry data."""

from typing import Any

from homeassistant.core import HomeAssistant

from . import L2ConfigEntry
from .const import CONF_EXPERIMENTAL, CONF_POLL_INTERVAL, DOMAIN


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: L2ConfigEntry
) -> dict[str, Any]:
    """Exclude keys, addresses, names, IDs, raw reports and exception messages."""
    coordinator = getattr(entry, "runtime_data", None)
    return {
        "integration": DOMAIN,
        "options": {
            CONF_EXPERIMENTAL: bool(entry.options.get(CONF_EXPERIMENTAL, False)),
            CONF_POLL_INTERVAL: int(entry.options.get(CONF_POLL_INTERVAL, 0)),
        },
        "runtime": None
        if coordinator is None
        else {
            "available": coordinator.available,
            "auth_failed": coordinator.auth_failed,
            "state_source": "reported" if coordinator.reported else "assumed",
            "last_read_success": coordinator.last_update_success,
            "last_error_type": type(coordinator.last_exception).__name__
            if coordinator.last_exception
            else None,
        },
    }
