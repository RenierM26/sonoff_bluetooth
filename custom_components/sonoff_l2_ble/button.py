"""A native button refreshes state through a new authenticated BLE session."""

from homeassistant.components.button import ButtonEntity
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import L2ConfigEntry
from .const import DOMAIN
from .entity import L2Entity

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant, entry: L2ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    async_add_entities([L2RefreshButton(entry, "refresh")])


class L2RefreshButton(L2Entity, ButtonEntity):
    _attr_translation_key = "refresh_state"
    _attr_icon = "mdi:refresh"

    @property
    def available(self) -> bool:
        # Recovery must work even with polling off / after HA marks the strip absent.
        return True

    async def async_press(self) -> None:
        await self.coordinator.async_refresh()
        if not self.coordinator.last_update_success:
            raise HomeAssistantError(
                "L2 did not provide a fresh state report",
                translation_domain=DOMAIN,
                translation_key="refresh_failed",
            )
