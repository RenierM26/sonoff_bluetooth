"""Native HA controls, report freshness, effect options and offline behaviour."""

import asyncio
from datetime import timedelta
from unittest.mock import AsyncMock, Mock, PropertyMock, patch

import pytest
from homeassistant.components.light.const import EFFECT_OFF, LightEntityFeature
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.data_entry_flow import FlowManagerIndexView
from homeassistant.helpers.update_coordinator import UpdateFailed
from test_protocol import KEY, FakeL2, bridge

from custom_components.sonoff_l2_ble import async_reload_entry
from custom_components.sonoff_l2_ble.button import L2RefreshButton
from custom_components.sonoff_l2_ble.button import async_setup_entry as setup_button
from custom_components.sonoff_l2_ble.config_flow import L2ConfigFlow, L2OptionsFlow
from custom_components.sonoff_l2_ble.coordinator import L2Coordinator
from custom_components.sonoff_l2_ble.light import L2Light
from custom_components.sonoff_l2_ble.number import L2Parameter
from custom_components.sonoff_l2_ble.number import async_setup_entry as setup_number
from custom_components.sonoff_l2_ble.protocol import ProtocolError, encrypt
from custom_components.sonoff_l2_ble.state import (
    EFFECTS,
    RHYTHMS,
    brightness,
    effect_name,
    sanitize_report,
    turn_on_params,
)

DATA = {"address": "AA:BB:CC:DD:EE:FF", "device_id": "1000000000"}


def configured(effects_enabled=False, interval=0):
    transport = Mock(send_update=AsyncMock(return_value={"error": 0}), read_state=AsyncMock())
    entry = Mock(
        data=DATA,
        options={"experimental_effects": effects_enabled, "poll_interval": interval},
        title="Synthetic strip",
    )
    coordinator = entry.runtime_data = L2Coordinator(Mock(), entry, transport)
    light = L2Light(entry)
    return entry, coordinator, transport, light


class ReportingL2(FakeL2):
    def __init__(self, message, early=False):
        super().__init__()
        self.message, self.early = message, early

    def notify(self, command, payload):
        import json

        if command == 3 and self.early:
            super().notify(16, encrypt(json.dumps(self.message), KEY))
        super().notify(command, payload)
        if command == 3 and not self.early:
            # An ACK-only notification before a report must never be used as state.
            super().notify(16, encrypt(json.dumps({"error": 0}), KEY))
            super().notify(16, encrypt(json.dumps(self.message), KEY))


@pytest.mark.parametrize("early", [False, True])
async def test_refresh_reads_this_connections_state_without_sending_control_or_query(early):
    peer = ReportingL2({"params": {"switch": "on", "mode": 0, "bright": 60}}, early)
    assert await bridge(peer).read_state() == {"switch": "on", "mode": 0, "bright": 60}
    assert peer.commands == [0, 2] and peer.disconnected


async def test_read_does_not_reuse_old_session_report():
    peer = ReportingL2({"params": {"switch": "on"}})
    transport = bridge(peer)
    assert (await transport.read_state())["switch"] == "on"
    peer.message = {"params": {"switch": "off"}}
    assert (await transport.read_state())["switch"] == "off"


@pytest.mark.parametrize(
    "message",
    [
        {"params": {"switch": "on", "bright": 101}},
        {"params": {"switch": "bad"}},
        {"params": {"switch": "on"}, "deviceid": "wrong"},
        [],
    ],
)
async def test_invalid_fresh_report_fails_and_disconnects(message):
    peer = ReportingL2(message)
    with pytest.raises(ProtocolError):
        await bridge(peer).read_state()
    assert peer.disconnected


async def test_missing_report_times_out_and_disconnects():
    peer = FakeL2()
    original_timeout = asyncio.timeout
    with (
        patch(
            "custom_components.sonoff_l2_ble.transport.asyncio.timeout",
            side_effect=lambda delay: original_timeout(0.01),
        ),
        pytest.raises(TimeoutError),
    ):
        await bridge(peer).read_state()
    assert peer.disconnected and peer.commands == [0, 2]


@pytest.mark.parametrize("bad", [True, "50", -1, 101, None])
def test_report_values_are_strict_and_only_known_fields_are_returned(bad):
    with pytest.raises(ProtocolError):
        sanitize_report({"params": {"switch": "on", "bright": bad}}, "1000000000")
    assert sanitize_report(
        {"params": {"switch": "off", "apikey": "do-not-retain"}, "apikey": "secret"}, "1000000000"
    ) == {"switch": "off"}


def test_exact_l2_preset_mapping_and_no_unbound_modes():
    assert len(EFFECTS) == 21 and set(EFFECTS.values()) == set(range(6, 27))
    assert EFFECTS["Reading"] == 7 and EFFECTS["Work"] == 18 and EFFECTS["Sunset"] == 26
    assert RHYTHMS == {
        "Rhythm classic": 0,
        "Rhythm mild": 1,
        "Rhythm dynamic": 2,
        "Rhythm disco": 3,
    }
    assert effect_name({"mode": 7}) == "Reading"
    assert effect_name({"mode": 4, "rhythmMode": 2}) == "Rhythm dynamic"
    assert effect_name({"mode": 1}) == EFFECT_OFF
    assert effect_name({"mode": 99}) is None
    assert brightness({"mode": 4, "bright": 80}) is None


async def test_explicitly_disabled_effect_controls_are_not_exposed_or_sent():
    entry, _, transport, light = configured()
    assert not light.supported_features & LightEntityFeature.EFFECT
    assert light.effect is None
    with pytest.raises(HomeAssistantError):
        await light.async_turn_on(effect="Reading")
    transport.send_update.assert_not_called()
    add = Mock()
    await setup_number(Mock(), entry, add)
    add.assert_not_called()


async def test_native_effect_and_brightness_preserve_mode_with_acked_state():
    _, coordinator, transport, light = configured(True)
    assert light.supported_features & LightEntityFeature.EFFECT
    assert "Reading" in light.effect_list and "Rhythm classic" in light.effect_list
    await light.async_turn_on(effect="Reading", brightness=153)
    transport.send_update.assert_awaited_with(
        {"switch": "on", "mode": 7, "speed07": 50, "bright07": 60}
    )
    assert light.effect == "Reading" and light.brightness == 153 and light.assumed_state
    await light.async_turn_on(brightness=102)
    transport.send_update.assert_awaited_with({"switch": "on", "mode": 7, "bright07": 40})
    assert coordinator.data["speed07"] == 50
    await light.async_turn_on(rgb_color=(3, 4, 5))
    assert light.effect == EFFECT_OFF and light.rgb_color == (3, 4, 5)
    transport.send_update.assert_awaited_with(
        {"switch": "on", "mode": 0, "colorR": 3, "colorG": 4, "colorB": 5}
    )


async def test_effect_ack_rejection_cannot_change_state():
    _, coordinator, transport, light = configured(True)
    coordinator.data = {"switch": "off", "mode": 1}
    transport.send_update.side_effect = ProtocolError("hidden credential")
    with pytest.raises(HomeAssistantError) as exc:
        await light.async_turn_on(effect="Reading")
    assert "hidden" not in str(exc.value)
    assert coordinator.data == {"switch": "off", "mode": 1} and light.is_on is False


async def test_native_rhythm_only_configures_controller_no_audio_stream_or_cloud():
    entry, coordinator, transport, light = configured(True)
    await light.async_turn_on(effect="Rhythm dynamic")
    transport.send_update.assert_awaited_once_with(
        {"switch": "on", "mode": 4, "rhythmMode": 2, "rhythmSensitive": 50}
    )
    sensitivity = L2Parameter(entry, "sensitivity")
    assert sensitivity.available and sensitivity.native_value == 50 and sensitivity.assumed_state
    await sensitivity.async_set_native_value(75.0)
    transport.send_update.assert_awaited_with({"mode": 4, "rhythmMode": 2, "rhythmSensitive": 75})
    assert light.effect == "Rhythm dynamic" and coordinator.data["rhythmSensitive"] == 75
    with pytest.raises(HomeAssistantError):
        await light.async_turn_on(brightness=128)
    await light.async_turn_on(effect="Solid")
    assert light.effect == EFFECT_OFF and not sensitivity.available


async def test_speed_tuning_preserves_power_and_effect_brightness():
    entry, coordinator, transport, _ = configured(True)
    coordinator.data = {"switch": "off", "mode": 19, "speed19": 80, "bright19": 33}
    speed = L2Parameter(entry, "speed")
    assert speed.available and speed.native_value == 80 and speed.native_min_value == 1
    await speed.async_set_native_value(60.0)
    transport.send_update.assert_awaited_once_with({"mode": 19, "speed19": 60, "bright19": 33})
    assert coordinator.data["switch"] == "off"
    for value in (0.0, 101.0, 3.5, float("nan")):
        with pytest.raises(HomeAssistantError):
            await speed.async_set_native_value(value)
    coordinator.data = {"switch": "on", "mode": 0}
    assert not speed.available and speed.native_value is None
    with pytest.raises(HomeAssistantError):
        await speed.async_set_native_value(60.0)


async def test_refresh_replaces_old_attributes_and_can_recover_after_failure():
    entry, coordinator, transport, light = configured()
    coordinator.data = {"switch": "on", "mode": 0, "bright": 90}
    transport.read_state.return_value = {"switch": "off"}
    # The real coordinator refresh path, not only its update method.
    await coordinator.async_refresh()
    assert light.is_on is False and light.brightness is None and not light.assumed_state
    assert light.rgb_color is None
    transport.read_state.side_effect = ProtocolError("secret")
    with pytest.raises(UpdateFailed) as exc:
        await coordinator._async_update_data()
    assert "secret" not in str(exc.value) and light.assumed_state and light.available
    # A failed read does not strand light control.
    await light.async_turn_on(rgb_color=(1, 2, 3))
    assert light.is_on and light.assumed_state
    transport.read_state.side_effect = None
    transport.read_state.return_value = {
        "switch": "on",
        "mode": 0,
        "colorR": 8,
        "colorG": 9,
        "colorB": 10,
    }
    button = L2RefreshButton(entry, "refresh")
    await button.async_press()
    assert light.rgb_color == (8, 9, 10) and not light.assumed_state


async def test_refresh_button_reports_failure_without_sending_an_update():
    entry, coordinator, transport, _ = configured()
    coordinator.async_refresh = AsyncMock()
    coordinator.last_update_success = False
    button = L2RefreshButton(entry, "refresh")
    assert button.available
    with pytest.raises(HomeAssistantError):
        await button.async_press()
    transport.send_update.assert_not_called()


async def test_native_platforms_and_poll_interval():
    entry, coordinator, _, _ = configured(True, 60)
    assert coordinator.update_interval == timedelta(seconds=60)
    add = Mock()
    await setup_number(Mock(), entry, add)
    assert len(add.call_args.args[0]) == 2
    await setup_button(Mock(), entry, add)
    assert isinstance(add.call_args.args[0][0], L2RefreshButton)
    assert configured()[1].update_interval is None


@pytest.mark.parametrize("interval", [0, 30, 60, 3600])
async def test_options_save_local_only_settings(interval):
    options = L2ConfigFlow.async_get_options_flow(Mock())
    result = await options.async_step_init(
        {"experimental_effects": True, "poll_interval": interval}
    )
    assert result["data"] == {
        "experimental_effects": True,
        "poll_interval": interval,
        "mtu_writes": False,
    }
    hass = Mock(config_entries=Mock(async_reload=AsyncMock()))
    entry = Mock(entry_id="synthetic")
    await async_reload_entry(hass, entry)
    hass.config_entries.async_reload.assert_awaited_once_with("synthetic")


@pytest.mark.parametrize("interval", [1, -1, 3601, 30.5, float("nan"), True])
async def test_options_invalid_interval_renders_native_form(interval):
    options = L2OptionsFlow()
    with patch.object(
        L2OptionsFlow, "config_entry", new_callable=PropertyMock, return_value=Mock(options={})
    ):
        form = await options.async_step_init({"poll_interval": interval})
    assert form["errors"] == {"base": "invalid_interval"}
    encoded = FlowManagerIndexView(Mock())._prepare_result_json(form)
    assert {field["name"] for field in encoded["data_schema"]} == {
        "experimental_effects",
        "poll_interval",
        "mtu_writes",
    }


def test_unknown_modes_and_conflicting_requests_do_not_invent_commands():
    for kwargs in ({"effect": "fake"}, {"effect": "Reading", "rgb": (1, 2, 3)}):
        with pytest.raises(ValueError):
            turn_on_params({}, effects_enabled=True, **kwargs)
    with pytest.raises(ValueError):
        turn_on_params({"mode": 4}, level=128)
    with pytest.raises(ValueError):
        turn_on_params({"mode": 5}, level=128)
    with pytest.raises(ValueError):
        turn_on_params({"mode": 7}, level=128, effects_enabled=False)
    assert turn_on_params({"mode": 7}, effects_enabled=False) == {"switch": "on"}
    assert turn_on_params({"mode": 1}, level=128)["mode"] == 1


async def test_mode_dependent_command_built_after_inflight_update_not_before():
    _, _, transport, light = configured(True)
    entered, finish = asyncio.Event(), asyncio.Event()

    async def send(params):
        if params.get("mode") == 19:
            entered.set()
            await finish.wait()
        return {"error": 0}

    transport.send_update.side_effect = send
    first = asyncio.create_task(light.async_turn_on(effect="Ocean"))
    await entered.wait()
    second = asyncio.create_task(light.async_turn_on(brightness=102))
    await asyncio.sleep(0)
    assert transport.send_update.await_count == 1
    finish.set()
    await asyncio.gather(first, second)
    assert transport.send_update.call_args.args[0] == {"switch": "on", "mode": 19, "bright19": 40}
    assert light.effect == "Ocean" and light.brightness == 102


async def test_fresh_read_and_control_are_serialized_without_old_report_overwrite():
    _, coordinator, transport, light = configured()
    entered, finish = asyncio.Event(), asyncio.Event()

    async def report():
        entered.set()
        await finish.wait()
        return {"switch": "off", "mode": 0}

    transport.read_state.side_effect = report
    read = asyncio.create_task(coordinator.async_refresh())
    await entered.wait()
    control = asyncio.create_task(light.async_turn_on(rgb_color=(1, 2, 3)))
    await asyncio.sleep(0)
    transport.send_update.assert_not_called()
    finish.set()
    await asyncio.gather(read, control)
    assert light.is_on and light.rgb_color == (1, 2, 3) and light.assumed_state


async def test_options_form_preserves_saved_local_preferences():
    options = L2OptionsFlow()
    with patch.object(
        L2OptionsFlow,
        "config_entry",
        new_callable=PropertyMock,
        return_value=Mock(options={"experimental_effects": True, "poll_interval": 60}),
    ):
        encoded = FlowManagerIndexView(Mock())._prepare_result_json(await options.async_step_init())
    assert [field["default"] for field in encoded["data_schema"]] == [True, 60, False]


async def test_number_mode_change_and_missing_rhythm_identity_are_rejected():
    entry, coordinator, transport, _ = configured(True)
    speed = L2Parameter(entry, "speed")
    coordinator.data = {"mode": 7}
    with pytest.raises(ValueError):
        speed._params({"mode": 0}, 50)
    sensitivity = L2Parameter(entry, "sensitivity")
    coordinator.data = {"mode": 4}
    with pytest.raises(HomeAssistantError):
        await sensitivity.async_set_native_value(20.0)
    transport.send_update.assert_not_called()


async def test_failed_control_after_report_marks_old_state_assumed_not_confirmed():
    _, coordinator, transport, light = configured()
    coordinator.data = {"switch": "off"}
    coordinator.reported = True
    transport.send_update.side_effect = TimeoutError()
    assert not light.assumed_state
    with pytest.raises(HomeAssistantError):
        await light.async_turn_on()
    assert light.is_on is False and light.assumed_state


async def test_cancelled_control_marks_cached_report_uncertain_and_propagates_cancel():
    _, coordinator, transport, light = configured()
    coordinator.data = {"switch": "off"}
    coordinator.reported = True
    entered = asyncio.Event()

    async def blocked(params):
        entered.set()
        await asyncio.Event().wait()

    transport.send_update.side_effect = blocked
    task = asyncio.create_task(light.async_turn_on())
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert light.is_on is False and light.assumed_state
    assert not coordinator.operation_lock.locked()


@pytest.mark.parametrize(
    "saved,enabled,interval",
    [
        ({}, True, 60),
        ({"experimental_effects": False, "poll_interval": 0}, False, 0),
        ({"experimental_effects": True, "poll_interval": 120}, True, 120),
        ({"poll_interval": 0}, True, 0),
        ({"experimental_effects": False}, False, 60),
    ],
)
async def test_effect_defaults_and_legacy_saved_options_agree_across_runtime_form_and_diagnostics(
    saved, enabled, interval
):
    from custom_components.sonoff_l2_ble.diagnostics import async_get_config_entry_diagnostics

    entry = Mock(data=DATA, options=saved, title="Synthetic strip")
    transport = Mock(send_update=AsyncMock(), read_state=AsyncMock())
    coordinator = entry.runtime_data = L2Coordinator(Mock(), entry, transport)
    light = L2Light(entry)
    assert coordinator.effects_enabled is enabled
    assert coordinator.update_interval == (timedelta(seconds=interval) if interval else None)
    assert bool(light.supported_features & LightEntityFeature.EFFECT) is enabled
    add = Mock()
    await setup_number(Mock(), entry, add)
    if enabled:
        assert len(add.call_args.args[0]) == 2
    else:
        add.assert_not_called()
    with patch.object(L2OptionsFlow, "config_entry", new_callable=PropertyMock, return_value=entry):
        form = await L2OptionsFlow().async_step_init()
        encoded = FlowManagerIndexView(Mock())._prepare_result_json(form)
        assert [field["default"] for field in encoded["data_schema"]] == [enabled, interval, False]
        result = await L2OptionsFlow().async_step_init(form["data_schema"]({}))
        assert result["data"] == {
            "experimental_effects": enabled,
            "poll_interval": interval,
            "mtu_writes": False,
        }
    exported = await async_get_config_entry_diagnostics(Mock(data={}), entry)
    assert exported["options"] == {
        "experimental_effects": enabled,
        "poll_interval": interval,
        "mtu_writes": False,
    }
    assert exported["runtime"]["polling_enabled"] is bool(interval)
    transport.read_state.assert_not_called()
    transport.send_update.assert_not_called()


def test_effect_command_builder_is_enabled_by_default():
    assert turn_on_params({}, effect="Reading")["mode"] == 7
    assert turn_on_params({}, effect="Rhythm classic")["mode"] == 4
