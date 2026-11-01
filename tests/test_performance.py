"""Synthetic timing/MTU and early-ACK regressions; not hardware speed claims."""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, PropertyMock, patch

import pytest
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.data_entry_flow import FlowManagerIndexView
from test_features import DATA
from test_protocol import KEY, FakeL2, bridge

from custom_components.sonoff_l2_ble import make_transport
from custom_components.sonoff_l2_ble.config_flow import L2OptionsFlow
from custom_components.sonoff_l2_ble.coordinator import L2Coordinator
from custom_components.sonoff_l2_ble.diagnostics import async_get_config_entry_diagnostics
from custom_components.sonoff_l2_ble.light import L2Light
from custom_components.sonoff_l2_ble.transport import L2Transport, write_chunk_size


class PacketPeer(FakeL2):
    def __init__(self, *, mtu=247, response=True, limit=244, **kwargs):
        super().__init__(**kwargs)
        self.mtu_size = mtu
        self.characteristic.properties = ["write" if response else "write-without-response"]
        self.characteristic.max_write_without_response_size = limit
        self.writes = []

    async def write_gatt_char(self, characteristic, packet, response):
        assert response == ("write" in characteristic.properties)
        assert len(packet) <= self.mtu_size - 3
        if not response:
            assert len(packet) <= characteristic.max_write_without_response_size
        self.writes.append(bytes(packet))
        # Reuse the synthetic frame parser; its original test assumes 20-byte writes.
        for offset in range(0, len(packet), 20):
            await super().write_gatt_char(characteristic, packet[offset : offset + 20], True)


@pytest.mark.parametrize(
    "mtu,response,limit,expected",
    [
        (23, True, 20, 20),
        (247, True, 20, 244),
        (517, True, 20, 244),
        (247, False, 64, 64),
        (23, False, 244, 20),
        (None, True, 20, 20),
        (True, True, 20, 20),
        ("247", True, 20, 20),
        (247, False, None, 20),
        (247, False, True, 20),
        (0, True, 20, 20),
    ],
)
def test_packet_limits_and_unknown_backend_fallback(mtu, response, limit, expected):
    client = SimpleNamespace(mtu_size=mtu)
    characteristic = SimpleNamespace(
        properties=["write" if response else "write-without-response"],
        max_write_without_response_size=limit,
    )
    assert write_chunk_size(client, characteristic, True) == expected
    assert write_chunk_size(client, characteristic, False) == 20


@pytest.mark.parametrize("response", [True, False])
async def test_opt_in_reduces_writes_preserves_exact_frames_and_ack_gate(response):
    peers = [PacketPeer(response=response, limit=64) for _ in range(2)]
    transports = [bridge(peer) for peer in peers]
    transports[1].mtu_writes = True
    for transport in transports:
        await transport.send_update({"switch": "on", "mode": 7, "speed07": 60})
    assert len(peers[1].writes) < len(peers[0].writes)
    assert max(map(len, peers[0].writes)) <= 20
    assert peers[0].request["params"] == peers[1].request["params"]
    assert all(peer.commands == [0, 2, 15] and peer.disconnected for peer in peers)
    for peer, transport in zip(peers, transports, strict=True):
        timing = transport.timings["update"]
        assert timing.outcome == timing.disconnect_outcome == "success"
        assert timing.write_calls == len(peer.writes)
        assert timing.bytes_written == sum(map(len, peer.writes))
        assert timing.total_ms >= timing.ack_ms >= timing.command_ms >= 0
        assert timing.connect_ms is not None and timing.authentication_ms is not None


async def test_partial_large_write_failure_never_replays_or_publishes_ack():
    peer = PacketPeer()
    original = peer.write_gatt_char

    async def failing(characteristic, packet, response):
        if peer.commands == [0, 2]:
            raise OSError("private backend detail")
        await original(characteristic, packet, response)

    peer.write_gatt_char = failing
    transport = bridge(peer)
    transport.mtu_writes = True
    transport.on_update_ack = Mock()
    with pytest.raises(OSError):
        await transport.send_update({"switch": "on"})
    transport.on_update_ack.assert_not_called()
    assert peer.commands == [0, 2] and peer.disconnected
    assert transport.timings["update"].outcome == "failed"
    assert transport.timings["update"].ack_ms is None
    assert not transport.lock.locked()


@pytest.mark.parametrize("cancel_cleanup", [False, True])
async def test_ha_state_published_at_ack_not_disconnect_and_next_command_waits(cancel_cleanup):
    peer = FakeL2()
    entered, finish = asyncio.Event(), asyncio.Event()

    async def disconnect():
        entered.set()
        await finish.wait()
        peer.disconnected = True

    peer.disconnect = disconnect
    transport = bridge(peer)
    entry = Mock(data=DATA, options={"poll_interval": 0})
    coordinator = entry.runtime_data = L2Coordinator(Mock(), entry, transport)
    light = L2Light(entry)
    listener = Mock()
    coordinator.async_add_listener(listener)
    control = asyncio.create_task(light.async_turn_on(rgb_color=(7, 8, 9)))
    await asyncio.wait_for(entered.wait(), 1)
    assert not control.done() and transport.lock.locked() and coordinator.operation_lock.locked()
    assert light.is_on and light.rgb_color == (7, 8, 9) and light.assumed_state
    assert coordinator.last_control_outcome == "acknowledged"
    listener.assert_called_once()
    if cancel_cleanup:
        control.cancel()
        with pytest.raises(asyncio.CancelledError):
            await control
        assert coordinator.last_control_outcome == "acknowledged"
        assert transport.timings["update"].disconnect_outcome == "cancelled"
    else:
        second = asyncio.create_task(light.async_turn_off())
        await asyncio.sleep(0)
        assert peer.commands == [0, 2, 15]  # No overlapping session or command replay.
        finish.set()
        await asyncio.gather(control, second)
        assert light.is_on is False
        assert listener.call_count == 2  # One update per matching ACK, not after cleanup too.
    assert not transport.lock.locked() and not coordinator.operation_lock.locked()


@pytest.mark.parametrize(
    "kwargs,outcome",
    [
        ({"reject_auth": True}, "auth_failed"),
        ({"error": 400}, "failed"),
        ({"malformed": True}, "failed"),
    ],
)
async def test_negative_ack_and_auth_never_publish_target_before_cleanup(kwargs, outcome):
    peer = FakeL2(**kwargs)
    transport = bridge(peer)
    entry = Mock(data=DATA, options={"poll_interval": 0})
    coordinator = entry.runtime_data = L2Coordinator(Mock(), entry, transport)
    coordinator.data = {"switch": "off"}
    with pytest.raises(HomeAssistantError):
        await L2Light(entry).async_turn_on()
    assert coordinator.data == {"switch": "off"}
    assert transport.timings["update"].outcome == outcome
    assert transport.timings["update"].ack_ms is None


async def test_connection_failure_timings_and_auth_only_does_not_update_state():
    transport = L2Transport(AsyncMock(side_effect=OSError("private")), "1000000000", KEY, KEY)
    with pytest.raises(OSError):
        await transport.authenticate()
    timing = transport.timings["auth"]
    assert timing.outcome == "failed" and timing.connect_ms is not None
    assert timing.disconnect_ms is None and timing.disconnect_outcome == "not_attempted"
    assert not transport.lock.locked()
    peer = FakeL2()
    transport = bridge(peer)
    transport.on_update_ack = Mock()
    await transport.authenticate()
    transport.on_update_ack.assert_not_called()
    assert transport.timings["auth"].command_ms is None


async def test_cancelled_queue_wait_does_not_release_someone_elses_lock():
    transport = bridge(FakeL2())
    await transport.lock.acquire()
    task = asyncio.create_task(transport.authenticate())
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert transport.lock.locked()
    assert transport.timings["auth"].outcome == "cancelled"
    transport.lock.release()


async def test_timing_diagnostics_are_read_only_and_explicitly_sanitize_unknown_fields():
    entry = Mock(data=DATA, options={})
    transport = bridge(FakeL2())
    entry.runtime_data = L2Coordinator(Mock(), entry, transport)
    await transport.send_update({"switch": "on"})
    timing = transport.timings["update"]
    timing.private = KEY
    timing.outcome = timing.disconnect_outcome = "private-error-text"
    timing.connect_ms = KEY
    timing.notify_ms = float("nan")
    timing.command_ms = True
    timing.report_ms = 10**1000
    transport.timings["private-operation"] = timing
    transport.timings["read"] = {"private": KEY}
    with patch.object(transport, "factory", AsyncMock()) as factory:
        exported = await async_get_config_entry_diagnostics(Mock(data={}), entry)
    factory.assert_not_called()
    payload = exported["runtime"]["transport_timings"]
    assert set(payload) == {"update"}
    assert payload["update"]["outcome"] == payload["update"]["disconnect_outcome"] == "unknown"
    assert all(
        payload["update"][field] is None
        for field in ("connect_ms", "notify_ms", "command_ms", "report_ms")
    )
    encoded = json.dumps(exported, allow_nan=False)
    assert KEY not in encoded and "private-" not in encoded


@pytest.mark.parametrize("enabled", [False, True])
async def test_native_mtu_option_roundtrip_and_transport_configuration(enabled):
    options = {"experimental_effects": True, "poll_interval": 60, "mtu_writes": enabled}
    flow = L2OptionsFlow()
    with patch.object(
        L2OptionsFlow, "config_entry", new_callable=PropertyMock, return_value=Mock(options=options)
    ):
        form = await flow.async_step_init()
        encoded = FlowManagerIndexView(Mock())._prepare_result_json(form)
        assert encoded["data_schema"][-1]["default"] is enabled
        assert (await flow.async_step_init(options))["data"] == options
    transport = make_transport(
        Mock(),
        {**DATA, "name": "Synthetic", "device_apikey": KEY, "account_apikey": KEY},
        mtu_writes=enabled,
    )
    assert transport.mtu_writes is enabled


async def test_disconnect_failure_does_not_revoke_matching_ack():
    peer = FakeL2()
    peer.disconnect = AsyncMock(side_effect=OSError("private-cleanup-error"))
    transport = bridge(peer)
    transport.on_update_ack = Mock()
    await transport.send_update({"switch": "on"})
    transport.on_update_ack.assert_called_once_with({"switch": "on"})
    assert transport.timings["update"].outcome == "success"
    assert transport.timings["update"].disconnect_outcome == "failed"


@pytest.mark.parametrize("attribute", ["mtu_size", "max_write_without_response_size"])
def test_unimplemented_optional_backend_properties_keep_conservative_writes(attribute):
    client = Mock()
    characteristic = Mock(properties=["write-without-response"])
    client.mtu_size = 247
    target = client if attribute == "mtu_size" else characteristic
    with patch.object(
        type(target), attribute, PropertyMock(side_effect=NotImplementedError), create=True
    ):
        assert write_chunk_size(client, characteristic, False) == 20
        assert write_chunk_size(client, characteristic, True) == 20
