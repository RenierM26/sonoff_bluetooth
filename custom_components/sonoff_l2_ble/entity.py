"""Shared device identity for native HA light, button and number entities."""

from homeassistant.helpers.device_registry import CONNECTION_BLUETOOTH, DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import L2ConfigEntry
from .const import DOMAIN
from .coordinator import L2Coordinator


class L2Entity(CoordinatorEntity[L2Coordinator]):
    _attr_has_entity_name = True

    def __init__(self, entry: L2ConfigEntry, key: str = "") -> None:
        super().__init__(entry.runtime_data)
        address = entry.data["address"]
        self._attr_unique_id = f"{address}_{key}" if key else address
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, address)},
            connections={(CONNECTION_BLUETOOTH, address)},
            manufacturer="SONOFF",
            model="L2 (UIID 137)",
            name=entry.title,
        )

    @property
    def available(self) -> bool:
        # A failed read must not strand the light or prevent a manual retry.
        # Individual commands still fail safely if Bluetooth is unreachable.
        return True
