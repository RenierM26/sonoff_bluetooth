"""UI setup and reconfiguration; validation authenticates without changing lights."""

from __future__ import annotations

import math
import re
from collections.abc import Mapping
from typing import Any

import probatio as vol
from bleak.exc import BleakError
from homeassistant import config_entries
from homeassistant.config_entries import ConfigFlowResult
from homeassistant.core import callback
from homeassistant.helpers import selector

from . import make_transport
from .const import (
    CONF_ACCOUNT_APIKEY,
    CONF_DEVICE_APIKEY,
    CONF_DEVICE_ID,
    CONF_EFFECTS_ENABLED,
    CONF_POLL_INTERVAL,
    DEFAULT_EFFECTS_ENABLED,
    DEFAULT_POLL_INTERVAL,
    DOMAIN,
)
from .discovery import (
    Candidate,
    ImportFailed,
    InvalidToken,
    bluetooth_addresses,
    sonofflan_candidates,
    token_candidates,
)
from .protocol import ProtocolError


def _schema(existing: dict[str, Any] | None = None) -> vol.Schema:
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

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: config_entries.ConfigEntry) -> L2OptionsFlow:
        return L2OptionsFlow()

    def __init__(self) -> None:
        self._candidates: list[Candidate] = []
        self._selected: Candidate | None = None
        self._addresses: list[str] = []

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        return self.async_show_menu(step_id="user", menu_options=["sonofflan", "token", "manual"])

    async def async_step_manual(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        return await self._step(user_input, reconfigure=False)

    async def async_step_sonofflan(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if not self._candidates:
            self._candidates = sonofflan_candidates(self.hass)
        return await self._select_device("sonofflan", user_input)

    async def async_step_token(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                self._candidates = await token_candidates(
                    self.hass, str(user_input.get("token", ""))
                )
            except InvalidToken:
                errors["base"] = "invalid_token"
            except ImportFailed:
                errors["base"] = "cloud_failed"
            else:
                return await self.async_step_device()
        return self.async_show_form(
            step_id="token",
            data_schema=vol.Schema(
                {
                    vol.Required("token"): selector.TextSelector(
                        selector.TextSelectorConfig(type=selector.TextSelectorType.PASSWORD)
                    )
                }
            ),
            errors=errors,
        )

    async def async_step_device(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        return await self._select_device("device", user_input)

    async def _select_device(
        self, step_id: str, user_input: dict[str, Any] | None
    ) -> ConfigFlowResult:
        if not self._candidates:
            return self.async_abort(reason="no_devices")
        errors: dict[str, str] = {}
        if user_input is not None:
            choice = str(user_input.get("device", ""))
            if choice in {str(i) for i in range(len(self._candidates))}:
                self._selected = self._candidates[int(choice)]
                self._addresses = bluetooth_addresses(self.hass, self._selected.device_id)
                return await self.async_step_confirm()
            errors["base"] = "invalid_device"
        options = [
            selector.SelectOptionDict(
                value=str(i), label=f"{candidate.name} ({candidate.device_id})"
            )
            for i, candidate in enumerate(self._candidates)
        ]
        return self.async_show_form(
            step_id=step_id,
            data_schema=vol.Schema(
                {
                    vol.Required("device"): selector.SelectSelector(
                        selector.SelectSelectorConfig(
                            options=options, mode=selector.SelectSelectorMode.DROPDOWN
                        )
                    )
                }
            ),
            errors=errors,
        )

    async def async_step_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        candidate = self._selected
        if candidate is None:
            return self.async_abort(reason="no_devices")
        errors: dict[str, str] = {}
        if user_input is not None:
            result = await self._step(
                {
                    "name": user_input.get("name", candidate.name),
                    "address": user_input.get("address", ""),
                    CONF_DEVICE_ID: candidate.device_id,
                    CONF_DEVICE_APIKEY: candidate.device_apikey,
                    CONF_ACCOUNT_APIKEY: candidate.account_apikey,
                },
                reconfigure=False,
            )
            if result["type"] != "form":
                self._candidates = []
                self._selected = None
                return result
            errors = result.get("errors") or {}
        # Retry discovery if the user powered on or moved the strip after selection.
        self._addresses = bluetooth_addresses(self.hass, candidate.device_id)
        default_address = self._addresses[0] if len(self._addresses) == 1 else ""
        existing = user_input or {}
        return self.async_show_form(
            step_id="confirm",
            data_schema=vol.Schema(
                {
                    vol.Required("name", default=existing.get("name", candidate.name)): str,
                    vol.Required(
                        "address", default=existing.get("address") or default_address
                    ): str,
                }
            ),
            description_placeholders={
                "device_id": candidate.device_id,
                "addresses": ", ".join(self._addresses) or "—",
            },
            errors=errors,
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        return await self._step(user_input, reconfigure=True)

    async def async_step_reauth(self, entry_data: Mapping[str, Any]) -> ConfigFlowResult:
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        return await self._step(user_input, reconfigure=False, reauth=True)

    async def _step(
        self, user_input: dict[str, Any] | None, *, reconfigure: bool, reauth: bool = False
    ) -> ConfigFlowResult:
        entry = (
            self._get_reauth_entry()
            if reauth
            else (self._get_reconfigure_entry() if reconfigure else None)
        )
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
            elif existing and (
                data["address"] != existing["address"]
                or data[CONF_DEVICE_ID] != existing[CONF_DEVICE_ID]
            ):
                # A different BLE MAC changes entity/device unique IDs. Do not
                # silently orphan registry entries or attach old automations to
                # a replacement strip. Add that device as a new integration.
                errors["base"] = "identity_mismatch"
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
                except BleakError, TimeoutError, OSError:
                    errors["base"] = "cannot_connect"
                except ProtocolError:
                    errors["base"] = "auth_failed"
                else:
                    if entry:
                        return self.async_update_reload_and_abort(
                            entry,
                            title=data["name"],
                            data_updates=data,
                            reason="reauth_successful" if reauth else "reconfigure_successful",
                        )
                    return self.async_create_entry(title=data["name"], data=data)
        return self.async_show_form(
            step_id="reauth_confirm" if reauth else ("reconfigure" if reconfigure else "manual"),
            data_schema=_schema(existing),
            errors=errors,
        )


class L2OptionsFlow(config_entries.OptionsFlow):
    """Local-only effect controls and optional conservative state polling."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            interval = user_input.get(CONF_POLL_INTERVAL, DEFAULT_POLL_INTERVAL)
            if (
                type(interval) not in (int, float)
                or not math.isfinite(interval)
                or interval != int(interval)
                or not (interval == 0 or 30 <= interval <= 3600)
            ):
                errors["base"] = "invalid_interval"
            else:
                return self.async_create_entry(
                    title="",
                    data={
                        CONF_EFFECTS_ENABLED: bool(
                            user_input.get(CONF_EFFECTS_ENABLED, DEFAULT_EFFECTS_ENABLED)
                        ),
                        CONF_POLL_INTERVAL: int(interval),
                    },
                )
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_EFFECTS_ENABLED,
                        default=self.config_entry.options.get(
                            CONF_EFFECTS_ENABLED, DEFAULT_EFFECTS_ENABLED
                        ),
                    ): bool,
                    vol.Required(
                        CONF_POLL_INTERVAL,
                        default=self.config_entry.options.get(
                            CONF_POLL_INTERVAL, DEFAULT_POLL_INTERVAL
                        ),
                    ): selector.NumberSelector(
                        selector.NumberSelectorConfig(
                            min=0, max=3600, step=30, mode=selector.NumberSelectorMode.BOX
                        )
                    ),
                }
            ),
            errors=errors,
        )
