"""Native HA coordinator; serializes reports and commands and has no cloud client."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from datetime import timedelta
from typing import TYPE_CHECKING, Any

from bleak.exc import BleakError
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import CONF_EXPERIMENTAL, CONF_POLL_INTERVAL, DOMAIN
from .protocol import ProtocolError

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant

    from .transport import L2Transport

_LOGGER = logging.getLogger(__name__)


class L2Coordinator(DataUpdateCoordinator[dict[str, Any]]):
    """An actual report replaces stale data; an ACK only makes requested state assumed."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, transport: L2Transport) -> None:
        interval = int(entry.options.get(CONF_POLL_INTERVAL, 0))
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=DOMAIN,
            update_interval=timedelta(seconds=max(30, interval)) if interval else None,
        )
        self.transport = transport
        self.experimental = bool(entry.options.get(CONF_EXPERIMENTAL, False))
        self.reported = False
        self.data = {}
        self.operation_lock = asyncio.Lock()

    async def _async_update_data(self) -> dict[str, Any]:
        async with self.operation_lock:
            try:
                data = await self.transport.read_state()
            except ProtocolError, BleakError, TimeoutError, OSError:
                self.reported = False
                raise UpdateFailed("L2 state refresh failed; check Bluetooth proximity") from None
            self.reported = True
            return data

    async def async_control(
        self, request: dict[str, Any] | Callable[[dict[str, Any]], dict[str, Any]]
    ) -> None:
        async with self.operation_lock:
            try:
                # Build mode-dependent commands only after preceding reads/writes.
                params = request(self.data) if callable(request) else request
            except ValueError:
                raise HomeAssistantError(
                    "Invalid L2 colour/effect combination; check mode and experimental options"
                ) from None
            try:
                await self.transport.send_update(params)
            except asyncio.CancelledError:
                self.reported = False
                self.async_update_listeners()
                raise
            except ProtocolError, BleakError, TimeoutError, OSError:
                # A lost ACK may mean the device changed despite our failed call.
                self.reported = False
                self.async_update_listeners()
                raise HomeAssistantError(
                    "L2 Bluetooth command failed; check proximity and credentials"
                ) from None
            self.reported = False
            self.async_set_updated_data({**self.data, **params})
