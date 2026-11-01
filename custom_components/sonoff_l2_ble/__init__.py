"""Local Bluetooth control of SONOFF L2 (UIID 137)."""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, cast

from homeassistant.config_entries import ConfigEntry

from .const import CONF_ACCOUNT_APIKEY, CONF_DEVICE_APIKEY, CONF_DEVICE_ID
from .protocol import ProtocolError
from .transport import GattClient, L2Transport

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

    from .coordinator import L2Coordinator

type L2ConfigEntry = ConfigEntry[L2Coordinator]


def make_transport(hass: HomeAssistant, data: Mapping[str, Any]) -> L2Transport:
    """Use HA's adapters, including existing active ESPHome Bluetooth proxies."""
    from bleak_retry_connector import BleakClientWithServiceCache, establish_connection
    from homeassistant.components import bluetooth

    address = data["address"]

    async def factory() -> GattClient:
        device = bluetooth.async_ble_device_from_address(hass, address, connectable=True)
        if device is None:
            raise ProtocolError("L2 not visible to a connectable Bluetooth adapter or proxy")
        info = bluetooth.async_last_service_info(hass, address, connectable=True)
        if (
            info
            and info.name.lower().startswith("ck_")
            and info.name[3:].lower() != data[CONF_DEVICE_ID].lower()
        ):
            raise ProtocolError("Bluetooth identity does not match the eWeLink device ID")
        client = await establish_connection(
            BleakClientWithServiceCache,
            device,
            data["name"],
            ble_device_callback=lambda: (
                bluetooth.async_ble_device_from_address(hass, address, connectable=True) or device
            ),
            timeout=20,
        )
        return cast(GattClient, client)

    return L2Transport(
        factory, data[CONF_DEVICE_ID], data[CONF_DEVICE_APIKEY], data[CONF_ACCOUNT_APIKEY]
    )


async def async_setup_entry(hass: HomeAssistant, entry: L2ConfigEntry) -> bool:
    """Set up a UI-configured light without changing its physical state."""
    from homeassistant.const import Platform

    from .coordinator import L2Coordinator

    entry.runtime_data = L2Coordinator(hass, entry, make_transport(hass, entry.data))
    entry.runtime_data.async_track_presence()
    entry.async_on_unload(entry.add_update_listener(async_reload_entry))
    # Best effort: an offline strip must not prevent adding its retry button/light.
    await entry.runtime_data.async_refresh()
    await hass.config_entries.async_forward_entry_setups(
        entry, [Platform.LIGHT, Platform.BUTTON, Platform.NUMBER]
    )
    return True


async def async_unload_entry(hass: HomeAssistant, entry: L2ConfigEntry) -> bool:
    """Unload the platform; the transport holds no persistent connection."""
    from homeassistant.const import Platform

    return await hass.config_entries.async_unload_platforms(
        entry, [Platform.LIGHT, Platform.BUTTON, Platform.NUMBER]
    )


async def async_reload_entry(hass: HomeAssistant, entry: L2ConfigEntry) -> None:
    """Apply local options without replacing the light/config entry identity."""
    await hass.config_entries.async_reload(entry.entry_id)
