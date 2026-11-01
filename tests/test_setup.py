"""HA adapter selection and entry lifecycle without a running HA server."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest

from custom_components.sonoff_l2_ble import async_setup_entry, async_unload_entry, make_transport
from custom_components.sonoff_l2_ble.light import async_setup_entry as setup_light
from custom_components.sonoff_l2_ble.protocol import ProtocolError

DATA = {
    "name": "Test strip",
    "address": "AA:BB:CC:DD:EE:FF",
    "device_id": "1000000000",
    "device_apikey": "synthetic-device",
    "account_apikey": "synthetic-account",
}


async def test_setup_and_unload_forward_native_platforms():
    hass = SimpleNamespace(
        config_entries=SimpleNamespace(
            async_forward_entry_setups=AsyncMock(),
            async_unload_platforms=AsyncMock(return_value=True),
        )
    )
    entry = Mock(data=DATA, options={}, title="Strip")
    coordinator = Mock(async_refresh=AsyncMock())
    with (
        patch("custom_components.sonoff_l2_ble.make_transport", return_value="transport"),
        patch(
            "custom_components.sonoff_l2_ble.coordinator.L2Coordinator", return_value=coordinator
        ),
    ):
        assert await async_setup_entry(hass, entry)
    assert entry.runtime_data is coordinator
    coordinator.async_refresh.assert_awaited_once()
    hass.config_entries.async_forward_entry_setups.assert_awaited_once_with(
        entry, ["light", "button", "number"]
    )
    assert await async_unload_entry(hass, entry)
    hass.config_entries.async_unload_platforms.assert_awaited_once_with(
        entry, ["light", "button", "number"]
    )


async def test_light_platform_adds_entity_without_device_command():
    entry = SimpleNamespace(data=DATA, runtime_data=Mock(), title="Test strip")
    add = Mock()
    await setup_light(Mock(), entry, add)
    assert len(add.call_args.args[0]) == 1
    entry.runtime_data.assert_not_called()


async def test_factory_missing_adapter_does_not_try_direct_scanning():
    with (
        patch(
            "homeassistant.components.bluetooth.async_ble_device_from_address", return_value=None
        ),
        pytest.raises(ProtocolError, match="not visible"),
    ):
        await make_transport(Mock(), DATA).factory()


async def test_factory_rejects_mismatched_advertisement_identity():
    with (
        patch(
            "homeassistant.components.bluetooth.async_ble_device_from_address", return_value=Mock()
        ),
        patch(
            "homeassistant.components.bluetooth.async_last_service_info",
            return_value=SimpleNamespace(name="ck_1000000001"),
        ),
        pytest.raises(ProtocolError, match="identity"),
    ):
        await make_transport(Mock(), DATA).factory()


@pytest.mark.parametrize("name", ["ck_1000000000", "Unknown name"])
async def test_factory_uses_ha_device_and_retry_connector(name):
    device = Mock()
    client = Mock()
    with (
        patch(
            "homeassistant.components.bluetooth.async_ble_device_from_address", return_value=device
        ),
        patch(
            "homeassistant.components.bluetooth.async_last_service_info",
            return_value=SimpleNamespace(name=name),
        ),
        patch(
            "bleak_retry_connector.establish_connection",
            new_callable=AsyncMock,
            return_value=client,
        ) as establish,
    ):
        assert await make_transport(Mock(), DATA).factory() is client
        assert establish.call_args.kwargs["timeout"] == 20
        assert establish.call_args.kwargs["ble_device_callback"]() is device
