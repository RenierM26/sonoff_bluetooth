"""Authenticated local GATT sessions with optional, bounded live-session reuse."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable, Coroutine, Iterator
from contextlib import contextmanager, suppress
from dataclasses import dataclass
from time import perf_counter, time_ns
from typing import Any, Protocol

from .const import DEFAULT_MTU_WRITES
from .protocol import (
    NOTIFY,
    SERVICE,
    WRITE,
    AuthenticationError,
    FrameBuffer,
    ProtocolError,
    auth_start,
    auth_token,
    decrypt,
    request,
)
from .state import sanitize_report

SESSION_TIMEOUT = 45
REUSE_IDLE_SECONDS = 3
REUSE_MAX_SECONDS = 15
_RESET_IDLE = "reset_idle_lease"


@dataclass
class SessionTiming:
    """Only relative durations/counters; no payloads, identifiers or wall-clock times."""

    reused_connection: bool = False
    retained_connection: bool = False
    outcome: str = "in_progress"
    disconnect_outcome: str = "not_attempted"
    queue_ms: float | None = None
    connect_ms: float | None = None
    notify_ms: float | None = None
    authentication_ms: float | None = None
    command_ms: float | None = None
    report_ms: float | None = None
    ack_ms: float | None = None
    disconnect_ms: float | None = None
    total_ms: float | None = None
    write_calls: int = 0
    bytes_written: int = 0
    chunk_size: int = 20


@contextmanager
def measure(timing: SessionTiming, field: str) -> Iterator[None]:
    started = perf_counter()
    try:
        yield
    finally:
        setattr(timing, field, round((perf_counter() - started) * 1000, 3))


def write_chunk_size(client: GattClient, characteristic: GattCharacteristic, enabled: bool) -> int:
    """Use adapter limits unless explicitly disabled; never exceed MTU/backend/244 bytes."""
    if not enabled:
        return 20
    try:
        mtu = getattr(client, "mtu_size", None)
    except AttributeError, NotImplementedError:
        return 20
    if type(mtu) is not int or mtu < 23:
        return 20
    size = min(mtu - 3, 244)
    if "write" not in characteristic.properties:
        try:
            limit = getattr(characteristic, "max_write_without_response_size", None)
        except AttributeError, NotImplementedError:
            return 20
        if type(limit) is not int or limit < 20:
            return 20
        size = min(size, limit)
    return size


class GattCharacteristic(Protocol):
    """The characteristic surface consumed by this transport."""

    @property
    def properties(self) -> list[str]: ...


class GattService(Protocol):
    def get_characteristic(self, uuid: str) -> GattCharacteristic | None: ...


class GattServices(Protocol):
    def get_service(self, uuid: str) -> GattService | None: ...


class GattClient(Protocol):
    """Bleak-compatible client; tests can supply a hardware-free peer."""

    @property
    def services(self) -> GattServices: ...
    async def start_notify(self, uuid: str, callback: Callable[[Any, bytearray], None]) -> None: ...
    async def write_gatt_char(
        self, char: GattCharacteristic, data: bytes, *, response: bool
    ) -> None: ...
    async def disconnect(self) -> None: ...


class Exchange(Protocol):
    async def __call__(
        self, packet: bytes, expected: int, timing: SessionTiming, sequence: str | None = None
    ) -> bytes | dict[str, Any]: ...


@dataclass
class _Connection:
    client: GattClient
    exchange: Exchange
    queue: asyncio.Queue[tuple[int, bytes] | ProtocolError]
    early_reports: list[bytes]
    chunk_size: int


class L2Transport:
    """Serialize operations; optionally retain a successful live control session briefly."""

    def __init__(
        self,
        factory: Callable[[], Awaitable[GattClient]],
        device_id: str,
        device_apikey: str,
        account_apikey: str,
        *,
        mtu_writes: bool = DEFAULT_MTU_WRITES,
        reuse_connection: bool = False,
    ) -> None:
        self.factory = factory
        self.device_id = device_id
        self.device_apikey = device_apikey
        self.account_apikey = account_apikey
        self.lock = asyncio.Lock()
        self.mtu_writes = mtu_writes
        self.timings: dict[str, SessionTiming] = {}
        self.on_update_ack: Callable[[dict[str, Any]], None] | None = None
        self.reuse_connection = reuse_connection
        self.create_task: Callable[[Coroutine[Any, Any, None]], asyncio.Task[None]] = (
            asyncio.create_task
        )
        self._connection: _Connection | None = None
        self._reuse_deadline = 0.0
        self._idle_task: asyncio.Task[None] | None = None
        self._tasks: set[asyncio.Task[None]] = set()
        self._active_task: asyncio.Task[Any] | None = None
        self._closed = False
        self._close_lock = asyncio.Lock()
        self._last_sequence = 0

    @property
    def connection_retained(self) -> bool:
        """In-memory ownership flag, not a fresh Bluetooth reachability probe."""
        return self._connection is not None

    def _cancel_idle(self) -> None:
        task, self._idle_task = self._idle_task, None
        if task is not None and task is not asyncio.current_task() and not task.done():
            task.cancel(_RESET_IDLE)

    async def _disconnect(self, client: GattClient, timing: SessionTiming) -> None:
        timing.disconnect_outcome = "in_progress"
        with measure(timing, "disconnect_ms"), suppress(Exception):
            try:
                async with asyncio.timeout(10):
                    await client.disconnect()
            except asyncio.CancelledError:
                timing.disconnect_outcome = "cancelled"
                raise
            except Exception:
                timing.disconnect_outcome = "failed"
                raise
            else:
                timing.disconnect_outcome = "success"

    async def _close_retained(self) -> None:
        connection = self._connection
        if connection is None:
            return
        timing = self.timings["lease_cleanup"] = SessionTiming()
        try:
            with measure(timing, "total_ms"):
                await self._disconnect(connection.client, timing)
        except asyncio.CancelledError:
            # Keep the handle owned so explicit shutdown can finish cancelled cleanup.
            timing.outcome = "cancelled"
            raise
        else:
            timing.outcome = "success" if timing.disconnect_outcome == "success" else "failed"
            self._connection = None

    async def _expire(self, deadline: float) -> None:
        try:
            await asyncio.sleep(max(0, deadline - asyncio.get_running_loop().time()))
            async with self.lock:
                await self._close_retained()
        except asyncio.CancelledError as err:
            current = asyncio.current_task()
            if err.args != (_RESET_IDLE,) or (current is not None and current.cancelling() > 1):
                # Native HA lifecycle cancellation must also release our owned slot.
                self._closed = True
                async with self.lock:
                    await self._close_retained()
            raise
        finally:
            if self._idle_task is asyncio.current_task():
                self._idle_task = None

    def _retain(self, connection: _Connection, timing: SessionTiming) -> bool:
        now = asyncio.get_running_loop().time()
        if not timing.reused_connection:
            self._reuse_deadline = now + REUSE_MAX_SECONDS
        self._connection = connection
        coroutine = self._expire(min(now + REUSE_IDLE_SECONDS, self._reuse_deadline))
        try:
            task = self.create_task(coroutine)
        except Exception:
            coroutine.close()
            self._connection = None
            return False
        self._idle_task = task
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return True

    async def async_close(self) -> None:
        """Stop new work, cancel active operations and join all owned lease tasks."""
        self._closed = True
        async with self._close_lock:
            active = self._active_task
            if active is not None and active is not asyncio.current_task() and not active.done():
                active.cancel()
                with suppress(asyncio.CancelledError, Exception):
                    await active
            self._cancel_idle()
            for task in list(self._tasks):
                if task is not asyncio.current_task():
                    with suppress(asyncio.CancelledError):
                        await task
                    self._tasks.discard(task)
            async with self.lock:
                await self._close_retained()

    async def authenticate(self) -> None:
        """Validate the device key without changing the light state."""
        await self._session(None)

    async def send_update(self, params: dict[str, Any]) -> dict[str, Any]:
        result = await self._session(params)
        assert result is not None
        return result

    async def query(self) -> dict[str, Any]:
        """Send a query; a successful ACK need not contain state parameters."""
        result = await self._session([], action="query")
        assert result is not None
        return result

    async def read_state(self) -> dict[str, Any]:
        """Read this fresh connection's unsolicited report without sending an update/query."""
        result = await self._session(None, read_state=True)
        assert result is not None
        return result

    async def _session(
        self,
        params: dict[str, Any] | list[Any] | None,
        action: str = "update",
        *,
        read_state: bool = False,
    ) -> dict[str, Any] | None:
        timing = SessionTiming()
        operation = "read" if read_state else ("auth" if params is None else action)
        self.timings[operation] = timing
        started = perf_counter()
        client: GattClient | None = None
        retained = False
        try:
            with measure(timing, "total_ms"):
                with measure(timing, "queue_ms"):
                    await self.lock.acquire()
                self._active_task = asyncio.current_task()
                try:
                    if self._closed:
                        raise ProtocolError("L2 transport is closed")
                    self._cancel_idle()
                    connection = self._connection
                    can_reuse = (
                        self.reuse_connection and action == "update" and isinstance(params, dict)
                    )
                    if connection is not None and (
                        not can_reuse
                        or asyncio.get_running_loop().time() >= self._reuse_deadline
                        or getattr(connection.client, "is_connected", False) is not True
                    ):
                        await self._close_retained()
                        connection = None
                    self._connection = None
                    if connection is None:
                        with measure(timing, "connect_ms"):
                            client = await self.factory()
                    else:
                        client = connection.client
                        timing.reused_connection = True
                    try:
                        async with asyncio.timeout(SESSION_TIMEOUT):
                            if connection is None:
                                connection = await self._open_connection(
                                    client, timing, read_state=read_state
                                )
                            timing.chunk_size = connection.chunk_size
                            result = await self._execute(
                                connection, params, action, timing, read_state=read_state
                            )
                            if (
                                action == "update"
                                and isinstance(params, dict)
                                and result is not None
                            ):
                                timing.ack_ms = round((perf_counter() - started) * 1000, 3)
                                if self.on_update_ack is not None:
                                    self.on_update_ack(params)
                            if (
                                can_reuse
                                and not self._closed
                                and getattr(client, "is_connected", False) is True
                                and (
                                    not timing.reused_connection
                                    or asyncio.get_running_loop().time() < self._reuse_deadline
                                )
                            ):
                                retained = timing.retained_connection = self._retain(
                                    connection, timing
                                )
                                if retained:
                                    timing.disconnect_outcome = "deferred"
                            return result
                    finally:
                        if not retained:
                            await self._disconnect(client, timing)
                finally:
                    self._active_task = None
                    self.lock.release()
        except asyncio.CancelledError:
            timing.outcome = "cancelled"
            raise
        except AuthenticationError:
            timing.outcome = "auth_failed"
            raise
        except Exception:
            timing.outcome = "failed"
            raise
        finally:
            if timing.outcome == "in_progress":
                timing.outcome = "success"

    async def _open_connection(
        self,
        client: GattClient,
        timing: SessionTiming,
        *,
        read_state: bool = False,
    ) -> _Connection:
        service = client.services.get_service(SERVICE)
        if service is None:
            raise ProtocolError("L2 control service AAAA not present")
        characteristic = service.get_characteristic(WRITE)
        if characteristic is None or service.get_characteristic(NOTIFY) is None:
            raise ProtocolError("L2 control characteristics BBB0/BBB1 not present")
        response = "write" in characteristic.properties
        if not response and "write-without-response" not in characteristic.properties:
            raise ProtocolError("L2 control characteristic is not writable")
        timing.chunk_size = write_chunk_size(client, characteristic, self.mtu_writes)
        queue: asyncio.Queue[tuple[int, bytes] | ProtocolError] = asyncio.Queue()
        buffer = FrameBuffer()

        def receive(_sender: Any, data: bytearray) -> None:
            try:
                for item in buffer.feed(bytes(data)):
                    queue.put_nowait(item)
            except ProtocolError as err:
                queue.put_nowait(err)

        early_reports: list[bytes] = []

        async def exchange(
            packet: bytes, expected: int, timing: SessionTiming, sequence: str | None = None
        ) -> bytes | dict[str, Any]:
            # No fallback/replay after a partial write or missing ACK.
            for offset in range(0, len(packet), timing.chunk_size):
                chunk = packet[offset : offset + timing.chunk_size]
                timing.write_calls += 1
                await client.write_gatt_char(characteristic, chunk, response=response)
                timing.bytes_written += len(chunk)
                if not response:
                    await asyncio.sleep(0.03)
            async with asyncio.timeout(12):
                while True:
                    item = await queue.get()
                    if isinstance(item, ProtocolError):
                        raise item
                    command, body = item
                    if command != expected:
                        if read_state and command == 16:
                            early_reports.append(body)
                        continue
                    if sequence is None:
                        return body
                    try:
                        message = json.loads(decrypt(body, self.device_apikey))
                    except ValueError, TypeError:
                        raise ProtocolError("Invalid BLE response JSON") from None
                    if not isinstance(message, dict):
                        raise ProtocolError("BLE response is not an object")
                    if str(message.get("sequence")) != sequence or "error" not in message:
                        continue
                    if message["error"] != 0:
                        raise ProtocolError("L2 rejected the Bluetooth command")
                    return message

        with measure(timing, "notify_ms"):
            await client.start_notify(NOTIFY, receive)
        with measure(timing, "authentication_ms"):
            app_code, packet = auth_start(self.device_apikey)
            challenge = await exchange(packet, 1, timing)
            assert isinstance(challenge, bytes)
            token_reply = await exchange(
                auth_token(self.device_apikey, app_code, challenge), 3, timing
            )
            if not isinstance(token_reply, bytes) or not token_reply:
                raise ProtocolError("Invalid L2 Bluetooth authentication response")
            if token_reply[0] != 0:
                raise AuthenticationError("L2 Bluetooth authentication was rejected")
        return _Connection(client, exchange, queue, early_reports, timing.chunk_size)

    async def _execute(
        self,
        connection: _Connection,
        params: dict[str, Any] | list[Any] | None,
        action: str,
        timing: SessionTiming,
        *,
        read_state: bool,
    ) -> dict[str, Any] | None:
        if read_state:
            # Reports buffered during authentication are from this fresh session.
            # Never reuse reports from a control session or a previous connection.
            with measure(timing, "report_ms"):
                return await self._read_report(connection.queue, connection.early_reports)
        if params is None:
            return None
        if timing.reused_connection:
            while not connection.queue.empty():
                item = connection.queue.get_nowait()
                if isinstance(item, ProtocolError):
                    raise item  # Poisoned session: fail before writing, never replay.
        # Rapid warm commands must never share a millisecond-based ACK sequence.
        self._last_sequence = max(time_ns() // 1_000_000, self._last_sequence + 1)
        sequence, packet = request(
            self.device_id,
            self.device_apikey,
            self.account_apikey,
            params,
            sequence=str(self._last_sequence),
            action=action,
        )
        with measure(timing, "command_ms"):
            result = await connection.exchange(packet, 16, timing, sequence)
        assert isinstance(result, dict)
        return result

    async def _read_report(
        self, queue: asyncio.Queue[tuple[int, bytes] | ProtocolError], early_reports: list[bytes]
    ) -> dict[str, Any]:
        async with asyncio.timeout(4):
            while True:
                if early_reports:
                    body = early_reports.pop(0)
                else:
                    item = await queue.get()
                    if isinstance(item, ProtocolError):
                        raise item
                    command, body = item
                    if command != 16:
                        continue
                try:
                    message = json.loads(decrypt(body, self.device_apikey))
                except ValueError, TypeError:
                    raise ProtocolError("Invalid L2 state JSON") from None
                if not isinstance(message, dict):
                    raise ProtocolError("Invalid L2 state object")
                if "params" not in message:
                    continue  # ACK-only messages are never state.
                return sanitize_report(message, self.device_id)
