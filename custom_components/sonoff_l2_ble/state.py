"""Sanitized L2 reports and source-derived effect parameters."""

from __future__ import annotations

from typing import Any

from homeassistant.components.light.const import EFFECT_OFF

from .protocol import ProtocolError, light_params

# eWeLink UIID137 recommendScene (module 5273); not generic modes 27-30.
EFFECTS = {
    "Vibrant": 6,
    "Reading": 7,
    "Leisure": 8,
    "Sunrise": 9,
    "Sunshine": 10,
    "Radiant": 11,
    "Dreamy": 12,
    "Candlelight": 13,
    "Night": 14,
    "Sunny": 15,
    "Festive": 16,
    "Vivid": 17,
    "Work": 18,
    "Ocean": 19,
    "Creek": 20,
    "Gentle": 21,
    "Passion": 22,
    "Joy": 23,
    "Rainbow": 24,
    "Moonlight": 25,
    "Sunset": 26,
}
RHYTHMS = {"Rhythm classic": 0, "Rhythm mild": 1, "Rhythm dynamic": 2, "Rhythm disco": 3}
STATIC_EFFECT = "Solid"


def integer(value: Any, low: int, high: int) -> bool:
    """Booleans and numeric strings are not device integers."""
    return type(value) is int and low <= value <= high


def sanitize_report(message: dict[str, Any], device_id: str) -> dict[str, Any]:
    """Require actual power state, identity when supplied, and no device error.

    Return only known, range-checked parameters, never remote credentials/metadata.
    An ACK without parameters is not state. Partial OFF reports are legitimate.
    """
    if message.get("deviceid", device_id) != device_id or message.get("error", 0) != 0:
        raise ProtocolError("L2 state report has invalid identity or status")
    params = message.get("params")
    if not isinstance(params, dict) or params.get("switch") not in ("on", "off"):
        raise ProtocolError("L2 did not provide a state report")
    result: dict[str, Any] = {"switch": params["switch"]}
    ranges = {
        "mode": (0, 255),
        "bright": (0, 100),
        "rhythmMode": (0, 3),
        "rhythmSensitive": (0, 100),
        "colorR": (0, 255),
        "colorG": (0, 255),
        "colorB": (0, 255),
    }
    for mode in EFFECTS.values():
        ranges[f"speed{mode:02}"] = (1, 100)
        ranges[f"bright{mode:02}"] = (0, 100)
    for key, (low, high) in ranges.items():
        if key in params:
            if not integer(params[key], low, high):
                raise ProtocolError("L2 state report contains an invalid value")
            result[key] = params[key]
    return result


def effect_name(data: dict[str, Any]) -> str | None:
    mode = data.get("mode")
    if mode in (0, 1):
        return EFFECT_OFF
    if mode == 4:
        return next(
            (name for name, index in RHYTHMS.items() if index == data.get("rhythmMode")), None
        )
    return next((name for name, index in EFFECTS.items() if index == mode), None)


def brightness(data: dict[str, Any]) -> int | None:
    mode = data.get("mode")
    if mode == 4:
        return None  # No validated brightness field in rhythm mode.
    value = data.get(f"bright{mode:02}") if mode in EFFECTS.values() else data.get("bright")
    return round(value * 255 / 100) if value is not None else None


def turn_on_params(
    data: dict[str, Any],
    *,
    level: int | None = None,
    rgb: tuple[int, int, int] | None = None,
    effect: str | None = None,
    effects_enabled: bool = True,
) -> dict[str, Any]:
    """Static colours exit an effect; brightness alone never silently cancels one."""
    static = effect in (STATIC_EFFECT, EFFECT_OFF)
    if effect is not None and not static and not effects_enabled:
        raise ValueError("Enable effects in integration options first")
    if effect is not None and not static and rgb is not None:
        raise ValueError("Choose an effect or an RGB colour, not both")
    if effect is not None and effect not in (STATIC_EFFECT, EFFECT_OFF, *EFFECTS, *RHYTHMS):
        raise ValueError("Unknown L2 effect")
    mode = data.get("mode")
    if effect in EFFECTS:
        mode = EFFECTS[effect]
    elif effect in RHYTHMS:
        mode = 4
    elif static or rgb is not None:
        mode = 0
    if mode in EFFECTS.values():
        if not effects_enabled:
            if level is not None:
                raise ValueError("Enable effect controls or select an RGB colour")
            return {"switch": "on"}
        params: dict[str, Any] = {"switch": "on", "mode": mode}
        if effect is not None:
            params[f"speed{mode:02}"] = data.get(f"speed{mode:02}", 50)
            params[f"bright{mode:02}"] = data.get(f"bright{mode:02}", 50)
        if level is not None:
            params[f"bright{mode:02}"] = light_params(True, level)["bright"]
        return params
    if mode == 4:
        if level is not None:
            raise ValueError("Brightness in rhythm mode is not validated; select an RGB colour")
        if effect is not None:
            return {
                "switch": "on",
                "mode": 4,
                "rhythmMode": RHYTHMS[effect],
                "rhythmSensitive": data.get("rhythmSensitive", 50),
            }
        return {"switch": "on"}
    if rgb is None and effect is None and level is not None and mode not in (None, 0, 1):
        raise ValueError("Brightness for this mode is unknown; select an RGB colour first")
    params = light_params(True, level, rgb)
    if static:
        params["mode"] = 0
    elif mode == 1 and rgb is None and level is not None:
        params["mode"] = 1
    return params
