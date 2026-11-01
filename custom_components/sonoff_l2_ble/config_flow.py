"""UI setup and reconfiguration; validation authenticates without changing lights."""

from __future__ import annotations

import re
from importlib import import_module
from typing import Any

from bleak.exc import BleakError
from homeassistant import config_entries
from homeassistant.config_entries import ConfigFlowResult
from homeassistant.helpers import selector

from . import make_transport
from .const import CONF_ACCOUNT_APIKEY, CONF_DEVICE_APIKEY, CONF_DEVICE_ID, DOMAIN
from .protocol import ProtocolError

# Select the schema library declared by this HA version, not whichever optional
# library happens to be installed. No process-wide compatibility shims.
_schema_hint = str(config_entries.ConfigFlow.async_show_form.__annotations__.get("data_schema", ""))
vol = import_module("probatio" if "probatio" in _schema_hint else "voluptuous")


def _schema(existing: dict[str, Any] | None = None) -> Any:
    """Never put saved credentials back into a frontend form."""
    existing = existing or {}
    password = selector.TextSelector(
        selector.TextSelectorConfig(type=selector.TextSelectorType.PASSWORD)
    )
    fields: dict[Any, Any] = {
        vol.Required("name", default=existing.get("name", "SONOFF L2")): str,
        vol.Required("address", default=existing.get("address", "")): str,
        vol.Required(CONF_DEVICE_ID, default=existing.get(CONF_DEVICE_ID, "")): str,
    }
    fields[vol.Optional(CONF_DEVICE_APIKEY) if existing else vol.Required(CONF_DEVICE_APIKEY)] = (
        password
    )
    fields[vol.Optional(CONF_ACCOUNT_APIKEY)] = password
    return vol.Schema(fields)


def _normalize(
    user_input: dict[str, Any], existing: dict[str, Any] | None = None
) -> dict[str, str]:
    """Normalize identifiers and apply documented blank-key behaviour."""
    existing = existing or {}
    data = {
        key: str(user_input.get(key, "")).strip()
        for key in ["name", "address", CONF_DEVICE_ID, CONF_DEVICE_APIKEY, CONF_ACCOUNT_APIKEY]
    }
    data["address"] = data["address"].upper()
    data[CONF_DEVICE_ID] = data[CONF_DEVICE_ID].lower()
    if existing:
        for key in [CONF_DEVICE_APIKEY, CONF_ACCOUNT_APIKEY]:
            if not data[key]:
                data[key] = existing[key]
    elif not data[CONF_ACCOUNT_APIKEY]:
        data[CONF_ACCOUNT_APIKEY] = data[CONF_DEVICE_APIKEY]
    return data


class L2ConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Create entries and update credentials without deleting the light."""

    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        return await self._step(user_input, reconfigure=False)

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        return await self._step(user_input, reconfigure=True)

    async def _step(
        self, user_input: dict[str, Any] | None, *, reconfigure: bool
    ) -> ConfigFlowResult:
        entry = self._get_reconfigure_entry() if reconfigure else None
        existing = dict(entry.data) if entry else None
        errors: dict[str, str] = {}
        if user_input is not None:
            data = _normalize(user_input, existing)
            if not re.fullmatch(r"(?:[0-9A-F]{2}:){5}[0-9A-F]{2}", data["address"]):
                errors["base"] = "invalid_address"
            elif not re.fullmatch(r"[0-9a-f]{10}", data[CONF_DEVICE_ID]):
                errors["base"] = "invalid_device_id"
            elif not all(data.values()):
                errors["base"] = "missing_fields"
            else:
                await self.async_set_unique_id(data["address"])
                if entry is None:
                    self._abort_if_unique_id_configured()
                elif any(
                    other.entry_id != entry.entry_id and other.unique_id == data["address"]
                    for other in self._async_current_entries()
                ):
                    return self.async_abort(reason="already_configured")
                try:
                    await make_transport(self.hass, data).authenticate()
                except (BleakError, TimeoutError, OSError):
                    errors["base"] = "cannot_connect"
                except ProtocolError:
                    errors["base"] = "auth_failed"
                else:
                    if entry:
                        return self.async_update_reload_and_abort(
                            entry, unique_id=data["address"], title=data["name"], data=data
                        )
                    return self.async_create_entry(title=data["name"], data=data)
        return self.async_show_form(
            step_id="reconfigure" if reconfigure else "user",
            data_schema=_schema(existing),
            errors=errors,
        )
