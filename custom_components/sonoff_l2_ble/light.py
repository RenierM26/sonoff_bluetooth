"""Native HA RGB/effects light with explicit reported versus assumed state."""

from typing import Any

from homeassistant.components.light import LightEntity
from homeassistant.components.light.const import EFFECT_OFF, ColorMode, LightEntityFeature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import L2ConfigEntry
from .entity import L2Entity
from .state import EFFECTS, RHYTHMS, brightness, effect_name, turn_on_params

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant, entry: L2ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    async_add_entities([L2Light(entry)])


class L2Light(L2Entity, LightEntity):
    _attr_name = None

    def __init__(self, entry: L2ConfigEntry) -> None:
        super().__init__(entry)
        self._attr_supported_color_modes = {ColorMode.RGB}
        if self.coordinator.experimental:
            self._attr_supported_features = LightEntityFeature.EFFECT
            self._attr_effect_list = [EFFECT_OFF, *EFFECTS, *RHYTHMS]

    @property
    def color_mode(self) -> ColorMode:
        mode = self.coordinator.data.get("mode")
        if self.coordinator.experimental:
            if mode in EFFECTS.values():
                return ColorMode.BRIGHTNESS
            if mode == 4 and self.effect:
                return ColorMode.ONOFF
        return ColorMode.RGB if mode in (0, 1) else ColorMode.UNKNOWN

    @property
    def is_on(self) -> bool | None:
        value = self.coordinator.data.get("switch")
        return value == "on" if value is not None else None

    @property
    def assumed_state(self) -> bool:
        return not self.coordinator.reported

    @property
    def brightness(self) -> int | None:
        return brightness(self.coordinator.data)

    @property
    def rgb_color(self) -> tuple[int, int, int] | None:
        data = self.coordinator.data
        if data.get("mode") not in (0, 1):
            return None
        if all(key in data for key in ("colorR", "colorG", "colorB")):
            return data["colorR"], data["colorG"], data["colorB"]
        return None

    @property
    def effect(self) -> str | None:
        return effect_name(self.coordinator.data) if self.coordinator.experimental else None

    async def async_turn_on(self, **kwargs: Any) -> None:
        if kwargs.get("brightness") == 0:
            await self.async_turn_off()
            return
        await self.coordinator.async_control(
            lambda data: turn_on_params(
                data,
                level=kwargs.get("brightness"),
                rgb=kwargs.get("rgb_color"),
                effect=kwargs.get("effect"),
                experimental=self.coordinator.experimental,
            )
        )

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.coordinator.async_control({"switch": "off"})
