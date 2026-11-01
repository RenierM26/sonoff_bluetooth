"""HA import/entity/config-form checks; no live HA or radio required."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

pytest.importorskip("homeassistant")
from homeassistant.exceptions import HomeAssistantError

from custom_components.sonoff_l2_ble.config_flow import L2ConfigFlow
from custom_components.sonoff_l2_ble.light import L2Light
from custom_components.sonoff_l2_ble.protocol import ProtocolError


def entity():
    transport = SimpleNamespace(send_update=AsyncMock(return_value={"error": 0}))
    light = L2Light(
        SimpleNamespace(
            runtime_data=transport, data={"address": "AA:BB:CC:DD:EE:FF"}, title="Test strip"
        )
    )
    light.async_write_ha_state = Mock()
    return light, transport


@pytest.mark.asyncio
async def test_ha_unknown_initial_state_then_acked_rgb():
    light, transport = entity()
    assert light.is_on is None and light.assumed_state and not light.should_poll
    await light.async_turn_on(brightness=128, rgb_color=(1, 2, 3))
    transport.send_update.assert_awaited_once_with(
        {"switch": "on", "mode": 0, "bright": 50, "colorR": 1, "colorG": 2, "colorB": 3}
    )
    assert light.is_on and light.brightness == 128 and light.rgb_color == (1, 2, 3)
    assert light.available


@pytest.mark.asyncio
async def test_ha_failed_ack_does_not_update_state():
    light, transport = entity()
    transport.send_update.side_effect = ProtocolError("rejected")
    with pytest.raises(HomeAssistantError):
        await light.async_turn_on()
    assert light.is_on is None
    light.async_write_ha_state.assert_not_called()


@pytest.mark.asyncio
async def test_ha_zero_brightness_means_off():
    light, transport = entity()
    await light.async_turn_on(brightness=0)
    transport.send_update.assert_awaited_once_with({"switch": "off"})
    assert light.is_on is False


@pytest.mark.asyncio
async def test_ha_form_renders_with_masked_credential_fields():
    flow = L2ConfigFlow()
    form = await flow.async_step_user()
    assert form["step_id"] == "user"
    schema = form["data_schema"]
    fields = {str(key): value for key, value in schema.schema.items()}
    assert fields["device_apikey"].config["type"] == "password"
    assert fields["account_apikey"].config["type"] == "password"
    invalid = await flow.async_step_user(
        {
            "name": "L2",
            "address": "not a MAC",
            "device_id": "test",
            "device_apikey": "test",
            "account_apikey": "test",
        }
    )
    assert invalid["errors"] == {"base": "invalid_address"}
