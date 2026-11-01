"""Configuration flow behaviour with isolated HA boundaries and synthetic keys."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest
from bleak.exc import BleakError
from homeassistant.data_entry_flow import AbortFlow

from custom_components.sonoff_l2_ble.config_flow import L2ConfigFlow
from custom_components.sonoff_l2_ble.protocol import ProtocolError

DATA = {
    "name": "Test strip",
    "address": "AA:BB:CC:DD:EE:FF",
    "device_id": "1000000000",
    "device_apikey": "synthetic-device-key",
    "account_apikey": "synthetic-account-key",
}


def flow():
    instance = L2ConfigFlow()
    instance.hass = Mock()
    instance.async_set_unique_id = AsyncMock()
    instance._abort_if_unique_id_configured = Mock()
    return instance


@pytest.mark.parametrize(
    ("field", "value", "error"),
    [
        ("address", "bad", "invalid_address"),
        ("device_id", "bad", "invalid_device_id"),
        ("name", " ", "missing_fields"),
        ("device_apikey", " ", "missing_fields"),
    ],
)
async def test_invalid_input_does_not_connect(field, value, error):
    instance = flow()
    with patch("custom_components.sonoff_l2_ble.config_flow.make_transport") as make:
        result = await instance.async_step_manual({**DATA, field: value})
    assert result["errors"] == {"base": error}
    make.assert_not_called()


async def test_success_normalizes_and_only_authenticates():
    instance = flow()
    device = SimpleNamespace(authenticate=AsyncMock(), send_update=AsyncMock())
    with patch("custom_components.sonoff_l2_ble.config_flow.make_transport", return_value=device):
        result = await instance.async_step_manual(
            {**DATA, "address": " aa:bb:cc:dd:ee:ff ", "name": " Strip "}
        )
    assert result["type"] == "create_entry"
    assert result["data"]["address"] == DATA["address"] and result["title"] == "Strip"
    device.authenticate.assert_awaited_once()
    device.send_update.assert_not_called()


async def test_blank_optional_account_key_defaults_to_device_key():
    instance = flow()
    device = SimpleNamespace(authenticate=AsyncMock())
    data = dict(DATA)
    data.pop("account_apikey")
    with patch("custom_components.sonoff_l2_ble.config_flow.make_transport", return_value=device):
        result = await instance.async_step_manual(data)
    assert result["data"]["account_apikey"] == DATA["device_apikey"]


@pytest.mark.parametrize(
    ("exception", "error"),
    [
        (ProtocolError("hidden-key"), "auth_failed"),
        (BleakError("hidden-key"), "cannot_connect"),
        (TimeoutError(), "cannot_connect"),
        (OSError(), "cannot_connect"),
    ],
)
async def test_connection_errors_are_safe_and_retryable(exception, error):
    instance = flow()
    with patch(
        "custom_components.sonoff_l2_ble.config_flow.make_transport",
        return_value=SimpleNamespace(authenticate=AsyncMock(side_effect=exception)),
    ):
        result = await instance.async_step_manual(DATA)
    assert result["errors"] == {"base": error}
    assert "hidden-key" not in str(result)


async def test_duplicate_aborts_before_authentication():
    instance = flow()
    instance._abort_if_unique_id_configured.side_effect = AbortFlow("already_configured")
    with (
        patch("custom_components.sonoff_l2_ble.config_flow.make_transport") as make,
        pytest.raises(AbortFlow),
    ):
        await instance.async_step_manual(DATA)
    make.assert_not_called()


def reconfigure_flow():
    instance = flow()
    entry = SimpleNamespace(data=DATA, entry_id="synthetic-entry", unique_id=DATA["address"])
    instance._get_reconfigure_entry = Mock(return_value=entry)
    instance._async_current_entries = Mock(return_value=[entry])
    instance.async_update_reload_and_abort = Mock(
        return_value={"type": "abort", "reason": "reconfigure_successful"}
    )
    return instance, entry


async def test_reconfigure_form_never_contains_saved_secrets():
    instance, _ = reconfigure_flow()
    result = await instance.async_step_reconfigure()
    assert result["step_id"] == "reconfigure"
    assert DATA["device_apikey"] not in repr(result["data_schema"].schema)
    assert DATA["account_apikey"] not in repr(result["data_schema"].schema)


async def test_reconfigure_preserves_blank_keys_and_reloads_entry():
    instance, entry = reconfigure_flow()
    device = SimpleNamespace(authenticate=AsyncMock())
    with patch("custom_components.sonoff_l2_ble.config_flow.make_transport", return_value=device):
        result = await instance.async_step_reconfigure(
            {**DATA, "name": "New name", "device_apikey": "", "account_apikey": ""}
        )
    assert result["reason"] == "reconfigure_successful"
    kwargs = instance.async_update_reload_and_abort.call_args.kwargs
    assert kwargs["data_updates"]["device_apikey"] == DATA["device_apikey"]
    assert kwargs["data_updates"]["account_apikey"] == DATA["account_apikey"]
    assert kwargs["title"] == "New name"
    assert instance.async_update_reload_and_abort.call_args.args == (entry,)


async def test_reconfigure_can_replace_keys():
    instance, _ = reconfigure_flow()
    with patch(
        "custom_components.sonoff_l2_ble.config_flow.make_transport",
        return_value=SimpleNamespace(authenticate=AsyncMock()),
    ):
        await instance.async_step_reconfigure(
            {**DATA, "device_apikey": "new-device", "account_apikey": "new-account"}
        )
    assert (
        instance.async_update_reload_and_abort.call_args.kwargs["data_updates"]["device_apikey"]
        == "new-device"
    )


async def test_reconfigure_rejects_another_entry_address():
    instance, _ = reconfigure_flow()
    instance._async_current_entries.return_value = [
        SimpleNamespace(entry_id="other", unique_id=DATA["address"])
    ]
    result = await instance.async_step_reconfigure(DATA)
    assert result["reason"] == "already_configured"


async def test_form_serializes_through_this_ha_frontend_schema_codec():
    """Catch a wrong schema-library instance even when form construction succeeds."""
    import json

    from homeassistant.helpers.data_entry_flow import FlowManagerIndexView

    instance = flow()
    result = await instance.async_step_manual()
    serialized = FlowManagerIndexView(Mock())._prepare_result_json(result)
    fields = serialized["data_schema"]
    key = next(field for field in fields if field["name"] == "device_apikey")
    assert key["selector"]["text"]["type"] == "password"
    assert DATA["device_apikey"] not in json.dumps(serialized)
