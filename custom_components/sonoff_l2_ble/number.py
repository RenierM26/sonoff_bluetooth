"""Native experimental effect speed and controller-rhythm sensitivity controls."""

from typing import Any

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import L2ConfigEntry
from .const import DOMAIN
from .entity import L2Entity
from .state import EFFECTS

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant, entry: L2ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    if entry.runtime_data.experimental:
        async_add_entities([L2Parameter(entry, "speed"), L2Parameter(entry, "sensitivity")])


class L2Parameter(L2Entity, NumberEntity):
    _attr_native_min_value = 0
    _attr_native_max_value = 100
    _attr_native_step = 1
    _attr_native_unit_of_measurement = "%"
    _attr_mode = NumberMode.SLIDER

    def __init__(self, entry: L2ConfigEntry, key: str) -> None:
        super().__init__(entry, key)
        self.key = key
        self._attr_native_min_value = 1 if key == "speed" else 0
        self._attr_translation_key = "effect_speed" if key == "speed" else "rhythm_sensitivity"

    @property
    def available(self) -> bool:
        mode = self.coordinator.data.get("mode")
        return (
            super().available
            and self.coordinator.experimental
            and (mode in EFFECTS.values() if self.key == "speed" else mode == 4)
        )

    @property
    def assumed_state(self) -> bool:
        return not self.coordinator.reported

    @property
    def native_value(self) -> float | None:
        data = self.coordinator.data
        if not self.available:
            return None
        key = f"speed{data['mode']:02}" if self.key == "speed" else "rhythmSensitive"
        value = data.get(key)
        return float(value) if value is not None else None

    async def async_set_native_value(self, value: float) -> None:
        if (
            not self.available
            or not self.native_min_value <= value <= 100
            or not float(value).is_integer()
        ):
            raise ServiceValidationError(
                "Select a compatible experimental effect first; value must be within the integer slider range",
                translation_domain=DOMAIN,
                translation_key="invalid_number",
            )
        await self.coordinator.async_control(lambda data: self._params(data, int(value)))

    def _params(self, data: dict[str, Any], value: int) -> dict[str, Any]:
        """Validate mode again inside the shared operation lock."""
        mode = data.get("mode")
        if not self.coordinator.experimental or (
            mode not in EFFECTS.values() if self.key == "speed" else mode != 4
        ):
            raise ValueError("The active L2 mode changed")
        params: dict[str, Any] = {"mode": mode}
        if self.key == "speed":
            params[f"speed{mode:02}"] = value
            params[f"bright{mode:02}"] = data.get(f"bright{mode:02}", 50)
        else:
            if "rhythmMode" not in data:
                raise ValueError("Refresh state or select a rhythm mode first")
            params.update(rhythmMode=data["rhythmMode"], rhythmSensitive=value)
        # Do not include switch:on: tuning a saved effect must not power on a light.
        return params
