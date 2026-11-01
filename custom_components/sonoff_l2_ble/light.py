"""ACK-gated, assumed-state RGB light."""

from __future__ import annotations

from typing import Any

from bleak.exc import BleakError
from homeassistant.components.light import LightEntity
from homeassistant.components.light.const import ColorMode
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.device_registry import CONNECTION_BLUETOOTH, DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import L2ConfigEntry
from .const import DOMAIN
from .protocol import ProtocolError, light_params


async def async_setup_entry(
    hass: HomeAssistant, entry: L2ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Add the UI-configured light."""
    async_add_entities([L2Light(entry)])


class L2Light(LightEntity):
    """Keep state unknown until a light command has been acknowledged."""

    _attr_has_entity_name = True
    _attr_name = None
    _attr_should_poll = False
    _attr_color_mode = ColorMode.RGB
    _attr_assumed_state = True

    def __init__(self, entry: L2ConfigEntry) -> None:
        self._attr_supported_color_modes = {ColorMode.RGB}
        self._transport = entry.runtime_data
        self._attr_is_on = None
        self._attr_brightness = None
        self._attr_rgb_color = None
        address = entry.data["address"]
        self._attr_unique_id = address
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, address)},
            connections={(CONNECTION_BLUETOOTH, address)},
            manufacturer="SONOFF",
            model="L2 (UIID 137)",
            name=entry.title,
        )

    async def _send(self, params: dict[str, Any]) -> None:
        try:
            await self._transport.send_update(params)
        except (ProtocolError, BleakError, TimeoutError, OSError):
            raise HomeAssistantError(
                "L2 Bluetooth command failed; check proximity and credentials"
            ) from None
        self._attr_available = True

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Send on, optionally setting brightness and RGB."""
        brightness = kwargs.get("brightness")
        if brightness == 0:
            await self.async_turn_off()
            return
        rgb = kwargs.get("rgb_color")
        await self._send(light_params(True, brightness, rgb))
        self._attr_is_on = True
        if brightness is not None:
            self._attr_brightness = brightness
        if rgb is not None:
            self._attr_rgb_color = tuple(rgb)
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Send off; only change reported state after the ACK."""
        await self._send(light_params(False))
        self._attr_is_on = False
        self.async_write_ha_state()
