"""Optional live-session ownership and recovery with synthetic peers only."""

import asyncio
import json
import struct
from unittest.mock import AsyncMock, Mock, PropertyMock, patch

import pytest
from homeassistant.helpers.data_entry_flow import FlowManagerIndexView
from test_features import DATA
from test_performance import PacketPeer
from test_protocol import KEY

from custom_components.sonoff_l2_ble import async_setup_entry, async_unload_entry
from custom_components.sonoff_l2_ble.config_flow import L2OptionsFlow
from custom_components.sonoff_l2_ble.coordinator import L2Coordinator
from custom_components.sonoff_l2_ble.diagnostics import async_get_config_entry_diagnostics
from custom_components.sonoff_l2_ble.light import L2Light
from custom_components.sonoff_l2_ble.protocol import AuthenticationError, ProtocolError, encrypt
from custom_components.sonoff_l2_ble.transport import L2Transport

MODULE = "custom_components.sonoff_l2_ble.transport"


class WarmPeer(PacketPeer):
    def __init__(self):
        super().__init__()
        self.is_connected = True
        self.closed = asyncio.Event()
        self.requests = []
        self.subscriptions = 0
        self.report = {"switch": "off", "mode": 0}

    async def start_notify(self, uuid, callback):
        self.subscriptions += 1
        await super().start_notify(uuid, callback)

    def notify(self, command, payload):
        super().notify(command, payload)
        if command == 3:
            super().notify(16, encrypt(json.dumps({"params": self.report}), KEY))

    async def write_gatt_char(self, characteristic, packet, response):
        previous = self.commands.count(15)
        await super().write_gatt_char(characteristic, packet, response)
        if self.commands.count(15) != previous:
            self.requests.append(self.request.copy())

    async def disconnect(self):
        await super().disconnect()
        self.is_connected = False
        self.closed.set()


@pytest.fixture
async def warm():
    peers = []

    async def create():
        peer = WarmPeer()
        peers.append(peer)
        return peer

    factory = AsyncMock(side_effect=create)
    transport = L2Transport(factory, "1000000000", KEY, KEY, reuse_connection=True)
    yield transport, peers
    await transport.async_close()
    assert not transport.connection_retained
    assert not transport.lock.locked()
    assert not transport._tasks


async def test_successive_commands_reuse_authentication_and_have_unique_ack_sequences(warm):
    transport, peers = warm
    with patch(f"{MODULE}.time_ns", return_value=1000000000000):
        await transport.send_update({"switch": "on"})
        first = transport.timings["update"]
        await transport.send_update({"switch": "off"})
    await asyncio.sleep(0)  # Process cancellation of the superseded lease timer.
    assert transport.factory.await_count == 1
    peer = peers[0]
    assert peer.commands == [0, 2, 15, 15] and peer.subscriptions == 1
    assert not peer.disconnected and transport.connection_retained
    assert len({request["sequence"] for request in peer.requests}) == 2
    second = transport.timings["update"]
    assert not first.reused_connection and second.reused_connection
    assert second.retained_connection and second.disconnect_outcome == "deferred"
    assert second.authentication_ms is second.connect_ms is second.notify_ms is None
    assert second.write_calls < first.write_calls
    assert second.ack_ms is not None


async def test_disabled_default_uses_fresh_clients_and_has_no_lease_tasks(warm):
    transport, peers = warm
    transport.reuse_connection = False
    await transport.send_update({"switch": "on"})
    await transport.send_update({"switch": "off"})
    assert transport.factory.await_count == 2
    assert all(peer.disconnected for peer in peers)
    assert not transport.connection_retained and not transport._tasks
    assert L2Transport(transport.factory, "1000000000", KEY, KEY).reuse_connection is False


async def test_idle_expiry_releases_slot_and_next_command_authenticates_fresh(warm, monkeypatch):
    transport, peers = warm
    monkeypatch.setattr(f"{MODULE}.REUSE_IDLE_SECONDS", 0.01)
    await transport.send_update({"switch": "on"})
    await asyncio.wait_for(peers[0].closed.wait(), 1)
    assert not transport.connection_retained
    assert transport.timings["lease_cleanup"].disconnect_outcome == "success"
    await transport.send_update({"switch": "off"})
    assert transport.factory.await_count == 2
    assert not transport.timings["update"].reused_connection


async def test_absolute_reuse_age_does_not_extend_with_a_command(warm):
    transport, peers = warm
    await transport.send_update({"switch": "on"})
    deadline = transport._reuse_deadline
    await transport.send_update({"switch": "off"})
    assert transport._reuse_deadline == deadline
    transport._reuse_deadline = asyncio.get_running_loop().time() - 1
    await transport.send_update({"switch": "on"})
    assert peers[0].disconnected and len(peers) == 2
    assert not transport.timings["update"].reused_connection


@pytest.mark.parametrize("operation", ["read_state", "authenticate", "query"])
async def test_reads_and_validation_close_warm_connection_and_always_use_fresh_session(
    warm, operation
):
    transport, peers = warm
    await transport.send_update({"switch": "on"})
    if operation == "read_state":
        assert await transport.read_state() == {"switch": "off", "mode": 0}
    else:
        await getattr(transport, operation)()
    assert len(peers) == 2 and all(peer.disconnected for peer in peers)
    assert not transport.connection_retained
    assert peers[1].commands == ([0, 2, 15] if operation == "query" else [0, 2])


async def test_dropped_connection_is_discarded_before_next_write(warm):
    transport, peers = warm
    await transport.send_update({"switch": "on"})
    peers[0].is_connected = False
    await transport.send_update({"switch": "off"})
    assert len(peers) == 2
    assert peers[0].commands == [0, 2, 15] and peers[0].disconnected
    assert not transport.timings["update"].reused_connection


@pytest.mark.parametrize("failure", ["negative_ack", "write_error", "poisoned_queue"])
async def test_failed_warm_command_closes_without_replay_then_next_call_recovers(warm, failure):
    transport, peers = warm
    await transport.send_update({"switch": "on"})
    peer = peers[0]
    if failure == "negative_ack":
        peer.error = 400
    elif failure == "write_error":
        peer.write_gatt_char = AsyncMock(side_effect=OSError("private detail"))
    else:
        peer.callback(None, struct.pack("<HH", 16, 65535))
    with pytest.raises((ProtocolError, OSError)):
        await transport.send_update({"switch": "off"})
    assert transport.factory.await_count == 1 and peer.disconnected
    assert not transport.connection_retained and transport.timings["update"].ack_ms is None
    assert len(peer.requests) == (2 if failure == "negative_ack" else 1)
    await transport.send_update({"switch": "off"})
    assert len(peers) == 2 and peers[1].requests[-1]["params"] == {"switch": "off"}


async def test_stale_ack_cannot_complete_warm_command_or_cause_automatic_retry(warm, monkeypatch):
    transport, peers = warm
    await transport.send_update({"switch": "on"})
    peer = peers[0]
    stale_sequence = peer.requests[-1]["sequence"]
    original = peer.notify

    def stale(command, payload):
        if command == 16:
            payload = encrypt(json.dumps({"sequence": stale_sequence, "error": 0}), KEY)
        original(command, payload)

    peer.notify = stale
    monkeypatch.setattr(f"{MODULE}.SESSION_TIMEOUT", 0.02)
    with pytest.raises(TimeoutError):
        await transport.send_update({"switch": "off"})
    assert transport.factory.await_count == 1 and peer.disconnected
    assert transport.timings["update"].ack_ms is None


async def test_idle_disconnect_and_new_command_cannot_overlap(warm, monkeypatch):
    transport, peers = warm
    monkeypatch.setattr(f"{MODULE}.REUSE_IDLE_SECONDS", 0.01)
    await transport.send_update({"switch": "on"})
    peer = peers[0]
    entered, finish = asyncio.Event(), asyncio.Event()
    original = peer.disconnect

    async def blocked():
        entered.set()
        await finish.wait()
        await original()

    peer.disconnect = blocked
    await asyncio.wait_for(entered.wait(), 1)
    command = asyncio.create_task(transport.send_update({"switch": "off"}))
    await asyncio.sleep(0)
    assert not command.done() and transport.factory.await_count == 1
    finish.set()
    await asyncio.wait_for(command, 1)
    assert peer.disconnected and transport.factory.await_count == 2


async def test_explicit_close_cancels_active_command_and_joins_owned_tasks(warm):
    transport, peers = warm
    await transport.send_update({"switch": "on"})
    entered = asyncio.Event()

    async def blocked(*args, **kwargs):
        entered.set()
        await asyncio.Event().wait()

    peers[0].write_gatt_char = blocked
    command = asyncio.create_task(transport.send_update({"switch": "off"}))
    await entered.wait()
    await asyncio.wait_for(transport.async_close(), 1)
    with pytest.raises(asyncio.CancelledError):
        await command
    assert peers[0].disconnected and not transport._tasks
    with pytest.raises(ProtocolError, match="closed"):
        await transport.send_update({"switch": "on"})


async def test_native_lifecycle_cancellation_of_idle_task_also_closes_lease(warm):
    transport, peers = warm
    await transport.send_update({"switch": "on"})
    task = transport._idle_task
    await asyncio.sleep(0)  # Let the task enter its cancellation handler.
    task.cancel("Config entry unloading")
    with pytest.raises(asyncio.CancelledError):
        await task
    assert peers[0].disconnected and not transport.connection_retained


async def test_cancelled_idle_disconnect_remains_owned_and_is_finished(warm, monkeypatch):
    transport, peers = warm
    monkeypatch.setattr(f"{MODULE}.REUSE_IDLE_SECONDS", 0.01)
    await transport.send_update({"switch": "on"})
    peer = peers[0]
    entered = asyncio.Event()
    calls = 0
    original = peer.disconnect

    async def first_blocks():
        nonlocal calls
        calls += 1
        if calls == 1:
            entered.set()
            await asyncio.Event().wait()
        await original()

    peer.disconnect = first_blocks
    await asyncio.wait_for(entered.wait(), 1)
    task = transport._idle_task
    task.cancel("Home Assistant stopping")
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, 1)
    assert calls == 2 and peer.disconnected and not transport.connection_retained


async def test_task_creation_failure_falls_back_to_immediate_cleanup_without_replaying(warm):
    transport, peers = warm
    transport.create_task = Mock(side_effect=RuntimeError("task creation unavailable"))
    result = await transport.send_update({"switch": "on"})
    assert result["error"] == 0 and peers[0].disconnected
    assert peers[0].commands == [0, 2, 15]
    assert not transport.timings["update"].retained_connection


@pytest.mark.parametrize("unload_success", [True, False])
async def test_native_setup_owns_tasks_and_unload_or_stop_releases_connection(warm, unload_success):
    transport, peers = warm
    hass = Mock(
        config_entries=Mock(
            async_forward_entry_setups=AsyncMock(),
            async_unload_platforms=AsyncMock(return_value=unload_success),
        )
    )
    entry = Mock(data=DATA, options={"reuse_connection": True, "poll_interval": 0})
    entry.async_create_background_task.side_effect = lambda hass, coroutine, name, eager_start: (
        asyncio.create_task(coroutine)
    )
    with (
        patch("custom_components.sonoff_l2_ble.make_transport", return_value=transport),
        patch.object(L2Coordinator, "async_track_presence"),
    ):
        assert await async_setup_entry(hass, entry)
    await entry.runtime_data.async_control({"switch": "on"})
    entry.async_create_background_task.assert_called_once()
    assert transport.connection_retained
    assert await async_unload_entry(hass, entry) is unload_success
    assert transport.connection_retained is not unload_success
    # Native stop callback also closes after failed unload and is safe after success.
    await hass.bus.async_listen_once.call_args.args[1](Mock())
    assert not transport.connection_retained and all(peer.disconnected for peer in peers)


@pytest.mark.parametrize("enabled", [False, True])
async def test_reuse_options_serialize_and_roundtrip_without_changing_other_options(enabled):
    options = {
        "experimental_effects": False,
        "poll_interval": 0,
        "mtu_writes": False,
        "reuse_connection": enabled,
    }
    with patch.object(
        L2OptionsFlow, "config_entry", new_callable=PropertyMock, return_value=Mock(options=options)
    ):
        flow = L2OptionsFlow()
        form = await flow.async_step_init()
        encoded = FlowManagerIndexView(Mock())._prepare_result_json(form)
        assert encoded["data_schema"][-1]["default"] is enabled
        assert (await flow.async_step_init(form["data_schema"]({})))["data"] == options


async def test_warm_coordinator_mode_dependent_commands_remain_serialized_and_diagnostics_read_only(
    warm,
):
    transport, peers = warm
    entry = Mock(data=DATA, options={"reuse_connection": True, "poll_interval": 0})
    coordinator = entry.runtime_data = L2Coordinator(Mock(), entry, transport)
    light = L2Light(entry)
    await light.async_turn_on(effect="Reading")
    await light.async_turn_on(brightness=153)
    assert peers[0].requests[-1]["params"] == {"switch": "on", "mode": 7, "bright07": 60}
    assert light.assumed_state and coordinator.last_read_outcome == "not_attempted"
    record = transport.timings["update"]
    record.reused_connection = "private injected text"
    with patch.object(transport, "factory", AsyncMock()) as factory:
        result = await async_get_config_entry_diagnostics(Mock(data={}), entry)
    factory.assert_not_called()
    assert result["runtime"]["connection_retained"] is True
    assert result["runtime"]["transport_timings"]["update"]["reused_connection"] is None
    assert "private injected" not in json.dumps(result, allow_nan=False)


async def test_rejected_auth_does_not_keep_a_connection(warm):
    transport, peers = warm

    async def rejected():
        peer = WarmPeer()
        peer.reject_auth = True
        peers.append(peer)
        return peer

    transport.factory = AsyncMock(side_effect=rejected)
    with pytest.raises(AuthenticationError):
        await transport.send_update({"switch": "on"})
    assert peers[0].disconnected and not transport.connection_retained and not transport._tasks


async def test_lifecycle_cancel_racing_timer_reset_and_warm_write_has_no_deadlock(warm):
    transport, peers = warm
    await transport.send_update({"switch": "on"})
    timer = transport._idle_task
    await asyncio.sleep(0)
    original = peers[0].write_gatt_char
    injected = False

    async def race(characteristic, packet, response):
        nonlocal injected
        if not injected:
            injected = True
            timer.cancel("Home Assistant stopping")
            await asyncio.sleep(0)
        await original(characteristic, packet, response)

    peers[0].write_gatt_char = race
    await asyncio.wait_for(transport.send_update({"switch": "off"}), 1)
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(timer, 1)
    assert peers[0].disconnected and not transport.connection_retained
    assert transport.factory.await_count == 1


async def test_failed_idle_cleanup_is_reported_and_never_used_for_reuse(warm, monkeypatch):
    transport, peers = warm
    monkeypatch.setattr(f"{MODULE}.REUSE_IDLE_SECONDS", 0.01)
    await transport.send_update({"switch": "on"})
    peers[0].disconnect = AsyncMock(side_effect=OSError("private disconnect detail"))
    await asyncio.wait_for(transport._idle_task, 1)
    assert transport.timings["lease_cleanup"].outcome == "failed"
    assert not transport.connection_retained
    await transport.send_update({"switch": "off"})
    assert transport.factory.await_count == 2
