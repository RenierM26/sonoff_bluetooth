"""A native button refreshes state through a new authenticated BLE session."""

from homeassistant.components.button import ButtonEntity
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import L2ConfigEntry
from .entity import L2Entity


async def async_setup_entry(
    hass: HomeAssistant, entry: L2ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    async_add_entities([L2RefreshButton(entry, "refresh")])


class L2RefreshButton(L2Entity, ButtonEntity):
    _attr_name = "Refresh state"
    _attr_icon = "mdi:refresh"

    async def async_press(self) -> None:
        await self.coordinator.async_refresh()
        if not self.coordinator.last_update_success:
            raise HomeAssistantError("L2 did not provide a fresh state report")
