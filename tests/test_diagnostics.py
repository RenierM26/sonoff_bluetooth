"""Diagnostic exports are read-only cached snapshots without personal identifiers."""

import asyncio
import json
from contextlib import ExitStack, contextmanager
from types import SimpleNamespace
from unittest.mock import ANY, Mock, patch

import pytest
from homeassistant.exceptions import HomeAssistantError
from test_features import configured

from custom_components.sonoff_l2_ble.diagnostics import (
    async_get_config_entry_diagnostics,
    async_get_device_diagnostics,
)
from custom_components.sonoff_l2_ble.protocol import SERVICE, ProtocolError

MODULE = "custom_components.sonoff_l2_ble.diagnostics.bluetooth"


@contextmanager
def cached_bluetooth(*, present=True, connectable=True, info=True):
    # Include sensitive extra fields on every source object; none may be dumped.
    advertisement = SimpleNamespace(
        rssi=-61,
        local_name="private-room-name",
        manufacturer_data={1: b"private-blob"},
    )
    scan_info = (
        SimpleNamespace(
            name="ck_1000000000",
            address="AA:BB:CC:DD:EE:FF",
            rssi=-61,
            service_uuids=[SERVICE],
            source="private-proxy-host",
        )
        if info
        else None
    )
    paths = (
        [SimpleNamespace(advertisement=advertisement, scanner=Mock(name="private-proxy-host"))]
        if connectable
        else []
    )
    values = {
        "async_scanner_count": {
            "side_effect": lambda hass, *, connectable: 2 if connectable else 3
        },
        "async_address_present": {
            "side_effect": lambda hass, address, *, connectable: (
                present and (not connectable or paths != [])
            )
        },
        "async_last_service_info": {"return_value": scan_info},
        "async_scanner_devices_by_address": {"return_value": paths},
    }
    with ExitStack() as stack:
        mocks = {
            name: stack.enter_context(patch(f"{MODULE}.{name}", **kwargs))
            for name, kwargs in values.items()
        }
        yield mocks, scan_info, paths


async def test_cached_bluetooth_details_and_both_native_download_hooks_are_safe():
    entry, coordinator, transport, _ = configured(True, 60)
    entry.data = {**entry.data, "token": "sensitive-token", "username": "private-person"}
    device = Mock(name="private-room-name", identifiers={("secret", "private-device-id")})
    with cached_bluetooth() as (calls, _, _):
        result = await async_get_config_entry_diagnostics(Mock(), entry)
        device_result = await async_get_device_diagnostics(Mock(), entry, device)
    assert device_result == result
    assert result["diagnostics_schema"] == 2
    assert result["bluetooth"] == {
        "status": "ready",
        "scanner_count": 3,
        "connectable_scanner_count": 2,
        "visible": True,
        "connectable": True,
        "matching_device_identity": True,
        "rssi_dbm": -61,
        "control_service_advertised": True,
        "connectable_path_count": 1,
        "connectable_path_rssi_dbm": [-61],
    }
    assert result["runtime"]["state_source"] == "unknown"
    assert result["runtime"]["last_read_success"] is None
    assert result["runtime"]["polling_enabled"]
    serialized = json.dumps(result)
    for private in (
        "AA:BB:CC:DD:EE:FF",
        "1000000000",
        "sensitive-token",
        "private-proxy-host",
        "private-room-name",
        "private-person",
        "private-device-id",
        "private-blob",
    ):
        assert private not in serialized
    calls["async_scanner_devices_by_address"].assert_called_with(
        ANY, "AA:BB:CC:DD:EE:FF", connectable=True
    )
    transport.read_state.assert_not_called()
    transport.send_update.assert_not_called()
    assert not coordinator.operation_lock.locked()


@pytest.mark.parametrize("present,connectable,info", [(False, False, False), (True, False, True)])
async def test_unseen_and_listen_only_paths_do_not_crash_or_claim_connectability(
    present, connectable, info
):
    entry, _, _, _ = configured()
    with cached_bluetooth(present=present, connectable=connectable, info=info):
        result = await async_get_config_entry_diagnostics(Mock(), entry)
    assert result["bluetooth"]["visible"] is present
    assert result["bluetooth"]["connectable"] is False
    assert result["bluetooth"]["connectable_path_count"] == 0
    if not info:
        assert result["bluetooth"]["matching_device_identity"] is None


@pytest.mark.parametrize("error", [KeyError("private-key"), RuntimeError("private-state")])
async def test_bluetooth_not_initialized_is_downloadable_even_without_runtime(error):
    entry = SimpleNamespace(data={"address": "AA:BB:CC:DD:EE:FF"}, options={})
    with patch(f"{MODULE}.async_scanner_count", side_effect=error):
        result = await async_get_config_entry_diagnostics(Mock(), entry)
    assert result["runtime"] is None and result["bluetooth"] == {"status": "not_ready"}
    assert "private" not in json.dumps(result)


async def test_identity_mismatch_invalid_rssi_and_unrecognised_error_stay_redacted():
    entry, coordinator, _, _ = configured()
    with cached_bluetooth() as (_, info, paths):
        info.name = "ck_wrong-private-identity"
        info.rssi = "private-value"
        paths[0].advertisement.rssi = 127
        private_error_type = type("private-error-name", (Exception,), {})
        coordinator.last_exception = private_error_type("private-error-message")
        coordinator.last_read_outcome = "private-outcome"
        coordinator.last_control_outcome = "private-outcome"
        result = await async_get_config_entry_diagnostics(Mock(), entry)
    assert result["bluetooth"]["matching_device_identity"] is False
    assert result["bluetooth"]["rssi_dbm"] is None
    assert result["bluetooth"]["connectable_path_rssi_dbm"] == []
    assert result["runtime"]["last_error_type"] == "OtherError"
    assert result["runtime"]["last_read_outcome"] == "unknown"
    assert result["runtime"]["last_control_outcome"] == "unknown"
    assert "private" not in json.dumps(result)


async def test_read_failure_is_not_misreported_as_read_success_after_control_ack():
    entry, coordinator, transport, light = configured()
    transport.read_state.side_effect = ProtocolError("private-payload")
    await coordinator.async_refresh()
    await light.async_turn_on(rgb_color=(1, 2, 3))
    with cached_bluetooth():
        result = await async_get_config_entry_diagnostics(Mock(), entry)
    assert result["runtime"]["last_update_success"] is True
    assert result["runtime"]["last_read_success"] is False
    assert result["runtime"]["last_read_outcome"] == "invalid_report"
    assert result["runtime"]["last_control_outcome"] == "acknowledged"
    assert result["runtime"]["state_source"] == "assumed"
    assert result["runtime"]["known_state_fields"] == {
        "power": True,
        "mode": True,
        "brightness": False,
        "rgb": True,
    }


async def test_download_while_read_in_flight_does_not_wait_for_or_start_io():
    entry, coordinator, transport, _ = configured()
    started = asyncio.Event()

    async def stalled():
        started.set()
        await asyncio.Event().wait()

    transport.read_state.side_effect = stalled
    task = asyncio.create_task(coordinator.async_refresh())
    await started.wait()
    with cached_bluetooth():
        result = await async_get_config_entry_diagnostics(Mock(), entry)
    assert result["runtime"]["operation_in_progress"]
    assert result["runtime"]["last_read_outcome"] == "in_progress"
    assert result["runtime"]["last_read_success"] is None
    assert transport.read_state.await_count == 1
    transport.send_update.assert_not_called()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert coordinator.last_read_outcome == "cancelled"
    assert not coordinator.operation_lock.locked()


async def test_last_control_failure_is_visible_without_exporting_error_messages():
    entry, _, transport, light = configured()
    transport.send_update.side_effect = TimeoutError("private-message")
    with pytest.raises(HomeAssistantError):
        await light.async_turn_on()
    with cached_bluetooth():
        result = await async_get_config_entry_diagnostics(Mock(), entry)
    assert result["runtime"]["last_control_outcome"] == "connection_failed"
    assert "private-message" not in json.dumps(result)


async def test_successful_read_exports_reported_presence_not_actual_light_state():
    entry, coordinator, transport, _ = configured()
    transport.read_state.return_value = {
        "switch": "on",
        "mode": 0,
        "bright": 73,
        "colorR": 31,
        "colorG": 41,
        "colorB": 59,
    }
    await coordinator.async_refresh()
    with cached_bluetooth():
        result = await async_get_config_entry_diagnostics(Mock(), entry)
    assert result["runtime"]["state_source"] == "reported"
    assert result["runtime"]["last_read_success"] is True
    assert result["runtime"]["last_read_outcome"] == "reported"
    assert all(result["runtime"]["known_state_fields"].values())
    assert "colorR" not in json.dumps(result) and "switch" not in json.dumps(result)
    assert not result["runtime"]["operation_in_progress"]


@pytest.mark.parametrize(
    "name,expected", [("CK_1000000000", True), ("ck_1000000001", False), ("Generic", None)]
)
async def test_advertised_identity_result_is_boolean_or_unknown_never_the_name(name, expected):
    entry, _, _, _ = configured()
    with cached_bluetooth() as (_, info, _):
        info.name = name
        result = await async_get_config_entry_diagnostics(Mock(), entry)
    assert result["bluetooth"]["matching_device_identity"] is expected
    assert name not in json.dumps(result)
