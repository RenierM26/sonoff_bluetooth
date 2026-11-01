"""Short-lived authenticated GATT sessions; no cloud or Wi-Fi strip control."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable, Iterator
from contextlib import contextmanager, suppress
from dataclasses import dataclass
from time import perf_counter
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


@dataclass
class SessionTiming:
    """Only relative durations/counters; no payloads, identifiers or wall-clock times."""

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


class L2Transport:
    """Serialize operations and release the proxy slot after every request."""

    def __init__(
        self,
        factory: Callable[[], Awaitable[GattClient]],
        device_id: str,
        device_apikey: str,
        account_apikey: str,
        *,
        mtu_writes: bool = DEFAULT_MTU_WRITES,
    ) -> None:
        self.factory = factory
        self.device_id = device_id
        self.device_apikey = device_apikey
        self.account_apikey = account_apikey
        self.lock = asyncio.Lock()
        self.mtu_writes = mtu_writes
        self.timings: dict[str, SessionTiming] = {}
        self.on_update_ack: Callable[[dict[str, Any]], None] | None = None

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
        try:
            with measure(timing, "total_ms"):
                with measure(timing, "queue_ms"):
                    await self.lock.acquire()
                try:
                    with measure(timing, "connect_ms"):
                        client = await self.factory()
                    try:
                        # Includes notify setup and all writes, not only response waits.
                        async with asyncio.timeout(SESSION_TIMEOUT):
                            result = await self._connected_session(
                                client, params, action, timing, read_state=read_state
                            )
                            if (
                                action == "update"
                                and isinstance(params, dict)
                                and result is not None
                            ):
                                timing.ack_ms = round((perf_counter() - started) * 1000, 3)
                                if self.on_update_ack is not None:
                                    # ACK is validated; publish before slow disconnect, not before ACK.
                                    self.on_update_ack(params)
                            return result
                    finally:
                        # Keep the session lock until bounded cleanup completes; no background tasks.
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
                finally:
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

    async def _connected_session(
        self,
        client: GattClient,
        params: dict[str, Any] | list[Any] | None,
        action: str,
        timing: SessionTiming,
        *,
        read_state: bool = False,
    ) -> dict[str, Any] | None:
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
            packet: bytes, expected: int, sequence: str | None = None
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
            challenge = await exchange(packet, 1)
            assert isinstance(challenge, bytes)
            token_reply = await exchange(auth_token(self.device_apikey, app_code, challenge), 3)
            if not isinstance(token_reply, bytes) or not token_reply:
                raise ProtocolError("Invalid L2 Bluetooth authentication response")
            if token_reply[0] != 0:
                raise AuthenticationError("L2 Bluetooth authentication was rejected")
        if read_state:
            # Reports buffered during authentication are from this fresh session.
            # Never reuse reports from a control session or a previous connection.
            with measure(timing, "report_ms"):
                return await self._read_report(queue, early_reports)
        if params is None:
            return None
        sequence, packet = request(
            self.device_id, self.device_apikey, self.account_apikey, params, action=action
        )
        with measure(timing, "command_ms"):
            result = await exchange(packet, 16, sequence)
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
