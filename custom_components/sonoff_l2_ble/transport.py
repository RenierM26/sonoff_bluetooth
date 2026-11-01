"""Short-lived authenticated GATT sessions; no cloud or Wi-Fi strip control."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from contextlib import suppress
from typing import Any, Protocol

from .protocol import (
    NOTIFY,
    SERVICE,
    WRITE,
    FrameBuffer,
    ProtocolError,
    auth_start,
    auth_token,
    decrypt,
    request,
)


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
    ) -> None:
        self.factory = factory
        self.device_id = device_id
        self.device_apikey = device_apikey
        self.account_apikey = account_apikey
        self.lock = asyncio.Lock()

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

    async def _session(
        self, params: dict[str, Any] | list[Any] | None, action: str = "update"
    ) -> dict[str, Any] | None:
        async with self.lock:
            client = await self.factory()
            try:
                return await self._connected_session(client, params, action)
            finally:
                # Cleanup failure must not mask the command/authentication result.
                with suppress(Exception):
                    async with asyncio.timeout(10):
                        await client.disconnect()

    async def _connected_session(
        self, client: GattClient, params: dict[str, Any] | list[Any] | None, action: str
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
        queue: asyncio.Queue[tuple[int, bytes] | ProtocolError] = asyncio.Queue()
        buffer = FrameBuffer()

        def receive(_sender: Any, data: bytearray) -> None:
            try:
                for item in buffer.feed(bytes(data)):
                    queue.put_nowait(item)
            except ProtocolError as err:
                queue.put_nowait(err)

        async def exchange(
            packet: bytes, expected: int, sequence: str | None = None
        ) -> bytes | dict[str, Any]:
            # Default ATT MTU is 23; 20-byte writes also work with larger MTUs.
            for offset in range(0, len(packet), 20):
                await client.write_gatt_char(
                    characteristic, packet[offset : offset + 20], response=response
                )
                if not response:
                    await asyncio.sleep(0.03)
            async with asyncio.timeout(12):
                while True:
                    item = await queue.get()
                    if isinstance(item, ProtocolError):
                        raise item
                    command, body = item
                    if command != expected:
                        continue
                    if sequence is None:
                        return body
                    try:
                        message = json.loads(decrypt(body, self.device_apikey))
                    except (ValueError, TypeError):
                        raise ProtocolError("Invalid BLE response JSON") from None
                    if not isinstance(message, dict):
                        raise ProtocolError("BLE response is not an object")
                    if str(message.get("sequence")) != sequence or "error" not in message:
                        continue
                    if message["error"] != 0:
                        raise ProtocolError("L2 rejected the Bluetooth command")
                    return message

        await client.start_notify(NOTIFY, receive)
        app_code, packet = auth_start(self.device_apikey)
        challenge = await exchange(packet, 1)
        assert isinstance(challenge, bytes)
        token_reply = await exchange(auth_token(self.device_apikey, app_code, challenge), 3)
        if not isinstance(token_reply, bytes) or not token_reply or token_reply[0] != 0:
            raise ProtocolError("L2 Bluetooth authentication was rejected")
        if params is None:
            return None
        sequence, packet = request(
            self.device_id, self.device_apikey, self.account_apikey, params, action=action
        )
        result = await exchange(packet, 16, sequence)
        assert isinstance(result, dict)
        return result
