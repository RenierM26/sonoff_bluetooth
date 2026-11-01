"""Regression checks for findings from the HA developer-guidance review."""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest
from homeassistant.components.light.const import EFFECT_OFF, ColorMode
from homeassistant.exceptions import (
    ConfigEntryAuthFailed,
    HomeAssistantError,
    ServiceValidationError,
)
from homeassistant.helpers.data_entry_flow import FlowManagerIndexView
from test_config_flow import DATA, flow, reconfigure_flow
from test_features import configured
from test_protocol import FakeL2, bridge

from custom_components.sonoff_l2_ble.button import L2RefreshButton
from custom_components.sonoff_l2_ble.diagnostics import async_get_config_entry_diagnostics
from custom_components.sonoff_l2_ble.number import L2Parameter
from custom_components.sonoff_l2_ble.protocol import AuthenticationError, ProtocolError

MODULE = "custom_components.sonoff_l2_ble"


async def test_presence_callbacks_mark_offline_and_rediscovery_unknown_then_recover():
    entry, coordinator, transport, light = configured(True)
    unsub_ad, unsub_missing = Mock(), Mock()
    with (
        patch(
            f"{MODULE}.coordinator.bluetooth.async_address_present", return_value=True
        ) as present,
        patch(
            f"{MODULE}.coordinator.bluetooth.async_register_advertisement_callback",
            return_value=unsub_ad,
        ) as register,
        patch(
            f"{MODULE}.coordinator.bluetooth.async_track_unavailable", return_value=unsub_missing
        ) as missing,
    ):
        coordinator.async_track_presence()
    assert light.available
    present.assert_called_once_with(coordinator.hass, DATA["address"], connectable=True)
    assert register.call_args.args[2] == DATA["address"]
    assert missing.call_args.kwargs == {"connectable": True}
    assert [call.args[0] for call in entry.async_on_unload.call_args_list][-2:] == [
        unsub_ad,
        unsub_missing,
    ]
    coordinator.data = {"switch": "on", "mode": 7}
    coordinator.reported = True
    speed = L2Parameter(entry, "speed")
    assert speed.available
    missing.call_args.args[1](Mock())
    assert not light.available and not speed.available
    assert light.is_on is None and light.assumed_state
    button = L2RefreshButton(entry, "refresh")
    assert button.available
    discovered = register.call_args.args[1]
    discovered(SimpleNamespace(connectable=False))
    assert not light.available
    discovered(SimpleNamespace(connectable=True))
    assert light.available and light.is_on is None
    assert not speed.available
    # An advertisement must never issue a GATT request or invent power state.
    transport.send_update.assert_not_called()
    transport.read_state.assert_not_called()
    await light.async_turn_on(rgb_color=(1, 2, 3))
    assert light.is_on and light.available


async def test_connected_without_valid_report_is_unknown_not_stale():
    _, coordinator, transport, light = configured()
    coordinator.reachable = True
    coordinator.reported = True
    coordinator.data = {"switch": "on", "mode": 0, "bright": 80}
    transport.read_state.side_effect = ProtocolError("do-not-echo")
    await coordinator.async_refresh()
    assert light.available and light.is_on is None and light.brightness is None
    assert not coordinator.last_update_success and light.assumed_state


@pytest.mark.parametrize("error", [TimeoutError(), OSError()])
async def test_failed_connection_unavailable_but_refresh_can_recover(error):
    entry, coordinator, transport, light = configured()
    transport.read_state.side_effect = error
    await coordinator.async_refresh()
    assert not light.available
    button = L2RefreshButton(entry, "refresh")
    assert button.available
    transport.read_state.side_effect = None
    transport.read_state.return_value = {"switch": "off"}
    await button.async_press()
    assert light.available and light.is_on is False


async def test_explicit_auth_rejection_is_distinct_and_disconnects():
    peer = FakeL2(reject_auth=True)
    with pytest.raises(AuthenticationError):
        await bridge(peer).authenticate()
    assert peer.disconnected


async def test_auth_rejection_requests_native_reauth_not_generic_retry():
    entry, coordinator, transport, light = configured()
    transport.read_state.side_effect = AuthenticationError("secret")
    with pytest.raises(ConfigEntryAuthFailed) as error:
        await coordinator._async_update_data()
    assert "secret" not in str(error.value) and coordinator.auth_failed
    assert not light.available
    transport.send_update.side_effect = AuthenticationError("secret")
    with pytest.raises(HomeAssistantError):
        await light.async_turn_on()
    entry.async_start_reauth.assert_called_once_with(coordinator.hass)
    assert not light.available


async def test_transient_failure_does_not_launch_reauth():
    entry, coordinator, transport, light = configured()
    transport.send_update.side_effect = TimeoutError()
    with pytest.raises(HomeAssistantError):
        await light.async_turn_on()
    assert not light.available and not coordinator.auth_failed
    entry.async_start_reauth.assert_not_called()


async def test_reauth_form_preserves_identity_and_never_returns_keys():
    instance = flow()
    entry = SimpleNamespace(data=DATA, entry_id="same-entry", unique_id=DATA["address"])
    instance._get_reauth_entry = Mock(return_value=entry)
    instance._async_current_entries = Mock(return_value=[entry])
    instance.async_update_reload_and_abort = Mock(
        return_value={"type": "abort", "reason": "reauth_successful"}
    )
    result = await instance.async_step_reauth(DATA)
    encoded = json.dumps(FlowManagerIndexView(Mock())._prepare_result_json(result))
    assert DATA["device_apikey"] not in encoded and DATA["account_apikey"] not in encoded
    assert result["step_id"] == "reauth_confirm"
    with patch(f"{MODULE}.config_flow.make_transport", return_value=Mock(authenticate=AsyncMock())):
        result = await instance.async_step_reauth_confirm({**DATA, "device_apikey": "replacement"})
    assert result["reason"] == "reauth_successful"
    assert instance.async_update_reload_and_abort.call_args.args == (entry,)
    assert (
        instance.async_update_reload_and_abort.call_args.kwargs["data_updates"]["device_apikey"]
        == "replacement"
    )
    assert "unique_id" not in instance.async_update_reload_and_abort.call_args.kwargs


@pytest.mark.parametrize("changes", [{"address": "11:22:33:44:55:66"}, {"device_id": "1000000001"}])
async def test_reconfigure_cannot_orphan_registry_or_rebind_to_different_strip(changes):
    instance, _ = reconfigure_flow()
    with patch(f"{MODULE}.config_flow.make_transport") as connect:
        result = await instance.async_step_reconfigure({**DATA, **changes})
    assert result["errors"] == {"base": "identity_mismatch"}
    connect.assert_not_called()
    instance.async_update_reload_and_abort.assert_not_called()


async def test_native_effect_color_modes_and_static_scene_restore():
    _, coordinator, transport, light = configured(True)
    assert light.color_mode == ColorMode.UNKNOWN
    await light.async_turn_on(effect="Reading", brightness=128)
    assert light.color_mode == ColorMode.BRIGHTNESS
    attrs = light.state_attributes
    assert attrs["color_mode"] == ColorMode.BRIGHTNESS and attrs["effect"] == "Reading"
    await light.async_turn_on(effect="Rhythm mild")
    assert light.color_mode == ColorMode.ONOFF
    assert light.state_attributes["color_mode"] == ColorMode.ONOFF
    assert light.brightness is None
    # HA scenes naturally restore effect:off together with RGB/brightness.
    await light.async_turn_on(effect=EFFECT_OFF, rgb_color=(8, 9, 10), brightness=153)
    assert light.effect == EFFECT_OFF and light.color_mode == ColorMode.RGB
    assert light.rgb_color == (8, 9, 10)
    transport.send_update.assert_awaited_with(
        {"switch": "on", "mode": 0, "bright": 60, "colorR": 8, "colorG": 9, "colorB": 10}
    )
    await light.async_turn_on(effect="Solid")  # Backward-compatible alias.
    assert light.effect == EFFECT_OFF
    assert coordinator.data["mode"] == 0


async def test_invalid_controls_use_service_validation_errors():
    _, _, _, light = configured(True)
    with pytest.raises(ServiceValidationError):
        await light.async_turn_on(effect="not-a-preset")


async def test_diagnostics_whitelist_excludes_credentials_identifiers_and_error_text():
    entry, coordinator, _, _ = configured(True, 60)
    entry.data = {**DATA, "token": "sensitive-token", "name": "private-room-name"}
    entry.options["unexpected_secret"] = "never-export"
    coordinator.last_exception = ProtocolError("do-not-echo")
    result = await async_get_config_entry_diagnostics(Mock(data={}), entry)
    encoded = json.dumps(result)
    for value in [
        *DATA.values(),
        "sensitive-token",
        "private-room-name",
        "never-export",
        "do-not-echo",
    ]:
        assert value not in encoded
    assert result["runtime"]["last_error_type"] == "ProtocolError"
    unloaded = SimpleNamespace(options={}, data={})
    assert (await async_get_config_entry_diagnostics(Mock(data={}), unloaded))["runtime"] is None


async def test_stalled_gatt_write_has_whole_session_deadline_and_releases_lock():
    peer = FakeL2()

    async def stalled(*args, **kwargs):
        await asyncio.Event().wait()

    peer.write_gatt_char = stalled
    transport = bridge(peer)
    with patch(f"{MODULE}.transport.SESSION_TIMEOUT", 0.01), pytest.raises(TimeoutError):
        await transport.authenticate()
    assert peer.disconnected and not transport.lock.locked()


async def test_completed_flow_http_result_strips_saved_keys():
    instance = flow()
    with patch(f"{MODULE}.config_flow.make_transport", return_value=Mock(authenticate=AsyncMock())):
        result = await instance.async_step_manual(DATA)
    response = FlowManagerIndexView(Mock())._prepare_result_json(result)
    assert "data" not in response
    encoded = json.dumps(response)
    assert DATA["device_apikey"] not in encoded and DATA["account_apikey"] not in encoded
