"""Native HA coordinator; serializes reports and commands and has no cloud client."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from datetime import timedelta
from typing import TYPE_CHECKING, Any, Literal

from bleak.exc import BleakError
from homeassistant.components import bluetooth
from homeassistant.core import callback
from homeassistant.exceptions import (
    ConfigEntryAuthFailed,
    HomeAssistantError,
    ServiceValidationError,
)
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import CONF_EXPERIMENTAL, CONF_POLL_INTERVAL, DOMAIN
from .protocol import AuthenticationError, ProtocolError

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant

    from .transport import L2Transport

_LOGGER = logging.getLogger(__name__)


type ReadOutcome = Literal[
    "not_attempted",
    "in_progress",
    "reported",
    "auth_failed",
    "connection_failed",
    "invalid_report",
    "cancelled",
]
type ControlOutcome = Literal[
    "not_attempted",
    "in_progress",
    "acknowledged",
    "auth_failed",
    "connection_failed",
    "protocol_error",
    "invalid_request",
    "cancelled",
]


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
        self.entry = entry
        self.reachable: bool | None = None
        self.auth_failed = False
        self.transport = transport
        self.experimental = bool(entry.options.get(CONF_EXPERIMENTAL, False))
        self.reported = False
        self.last_read_outcome: ReadOutcome = "not_attempted"
        self.last_control_outcome: ControlOutcome = "not_attempted"
        self.data = {}
        self.operation_lock = asyncio.Lock()

    @callback
    def async_track_presence(self) -> None:
        """Observe HA's existing scanner; never connect or start our own scanner."""
        address = self.entry.data["address"]
        self.reachable = bluetooth.async_address_present(self.hass, address, connectable=True)

        @callback
        def discovered(info: bluetooth.BluetoothServiceInfoBleak) -> None:
            if info.connectable and self.reachable is not True:
                self.data = {}  # Rediscovery is not a device-state report.
                self.reachable = True
                self.async_update_listeners()

        @callback
        def unavailable(info: bluetooth.BluetoothServiceInfoBleak) -> None:
            self.reachable = False
            self.reported = False
            self.data = {}
            self.async_update_listeners()

        self.entry.async_on_unload(
            bluetooth.async_register_advertisement_callback(self.hass, discovered, address)
        )
        self.entry.async_on_unload(
            bluetooth.async_track_unavailable(self.hass, unavailable, address, connectable=True)
        )

    @property
    def available(self) -> bool:
        return not self.auth_failed and (
            self.reachable if self.reachable is not None else self.last_update_success
        )

    async def _async_update_data(self) -> dict[str, Any]:
        async with self.operation_lock:
            self.last_read_outcome = "in_progress"
            try:
                data = await self.transport.read_state()
            except asyncio.CancelledError:
                self.last_read_outcome = "cancelled"
                raise
            except AuthenticationError:
                self.last_read_outcome = "auth_failed"
                self.reported = False
                self.auth_failed = True
                raise ConfigEntryAuthFailed(
                    "L2 authentication was rejected",
                    translation_domain=DOMAIN,
                    translation_key="auth_failed",
                ) from None
            except BleakError, TimeoutError, OSError:
                self.last_read_outcome = "connection_failed"
                self.reported = False
                self.reachable = False
                raise UpdateFailed("L2 state refresh failed; check Bluetooth proximity") from None
            except ProtocolError:
                self.last_read_outcome = "invalid_report"
                # Connected but missing/invalid state means UNKNOWN, not old state.
                # An advertising command-capable strip can still accept controls.
                self.data = {}
                self.reported = False
                raise UpdateFailed("L2 did not provide a valid state report") from None
            self.reachable = True
            self.auth_failed = False
            self.reported = True
            self.last_read_outcome = "reported"
            return data

    async def async_control(
        self, request: dict[str, Any] | Callable[[dict[str, Any]], dict[str, Any]]
    ) -> None:
        async with self.operation_lock:
            try:
                # Build mode-dependent commands only after preceding reads/writes.
                params = request(self.data) if callable(request) else request
            except ValueError:
                self.last_control_outcome = "invalid_request"
                raise ServiceValidationError(
                    "Invalid L2 colour/effect combination; check mode and experimental options",
                    translation_domain=DOMAIN,
                    translation_key="invalid_control",
                ) from None
            self.last_control_outcome = "in_progress"
            try:
                await self.transport.send_update(params)
            except AuthenticationError:
                self.last_control_outcome = "auth_failed"
                self.reported = False
                self.auth_failed = True
                self.async_update_listeners()
                self.entry.async_start_reauth(self.hass)
                raise HomeAssistantError(
                    "L2 authentication was rejected",
                    translation_domain=DOMAIN,
                    translation_key="auth_failed",
                ) from None
            except asyncio.CancelledError:
                self.last_control_outcome = "cancelled"
                self.reported = False
                self.async_update_listeners()
                raise
            except (ProtocolError, BleakError, TimeoutError, OSError) as err:
                self.last_control_outcome = (
                    "protocol_error" if isinstance(err, ProtocolError) else "connection_failed"
                )
                # A lost ACK may mean the device changed despite our failed call.
                self.reported = False
                if not isinstance(err, ProtocolError):
                    self.reachable = False
                self.async_update_listeners()
                raise HomeAssistantError(
                    "L2 Bluetooth command failed; check proximity and credentials",
                    translation_domain=DOMAIN,
                    translation_key="command_failed",
                ) from None
            self.reachable = True
            self.auth_failed = False
            self.reported = False
            self.last_control_outcome = "acknowledged"
            self.async_set_updated_data({**self.data, **params})
