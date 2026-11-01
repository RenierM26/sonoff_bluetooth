"""L2 BLE codec reconstructed from eWeLink 5.29.1, modules 2190-94/4826.

Experimental: auth/control and subsequent state tested on one physical L2.
"""

from __future__ import annotations

import base64
import hashlib
import json
import struct
import time
from typing import Any
from uuid import uuid4

from cryptography.hazmat.primitives import padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

SERVICE = "0000aaaa-0000-1000-8000-00805f9b34fb"
WRITE = "0000bbb0-0000-1000-8000-00805f9b34fb"
NOTIFY = "0000bbb1-0000-1000-8000-00805f9b34fb"
IV = b"0000000000000000"  # Sixteen ASCII zeroes, NOT sixteen null bytes.


class ProtocolError(Exception):
    """Invalid frame, credentials, or device response; never contains secrets."""


def encrypt(text: str, device_apikey: str) -> bytes:
    key = hashlib.md5(device_apikey.encode(), usedforsecurity=False).digest()
    padder = padding.PKCS7(128).padder()
    padded = padder.update(text.encode()) + padder.finalize()
    cipher = Cipher(algorithms.AES(key), modes.CBC(IV)).encryptor()
    return base64.b64encode(cipher.update(padded) + cipher.finalize())


def decrypt(payload: bytes, device_apikey: str) -> str:
    try:
        ciphertext = base64.b64decode(payload, validate=True)
        key = hashlib.md5(device_apikey.encode(), usedforsecurity=False).digest()
        cipher = Cipher(algorithms.AES(key), modes.CBC(IV)).decryptor()
        padded = cipher.update(ciphertext) + cipher.finalize()
        unpadder = padding.PKCS7(128).unpadder()
        return (unpadder.update(padded) + unpadder.finalize()).decode()
    except ValueError, UnicodeError:
        raise ProtocolError("Cannot decrypt BLE response; check the device apikey") from None


def frame(command: int, payload: bytes) -> bytes:
    if len(payload) > 65535:
        raise ProtocolError("BLE payload too large")
    return struct.pack("<HH", command, len(payload)) + payload


class FrameBuffer:
    """Reassemble ATT notifications, including partial headers/coalesced frames."""

    def __init__(self) -> None:
        self.data = bytearray()

    def feed(self, data: bytes) -> list[tuple[int, bytes]]:
        self.data.extend(data)
        frames = []
        while len(self.data) >= 4:
            command, length = struct.unpack_from("<HH", self.data)
            if length > 16384:
                self.data.clear()
                raise ProtocolError("Invalid BLE response length")
            if len(self.data) < length + 4:
                break
            frames.append((command, bytes(self.data[4 : length + 4])))
            del self.data[: length + 4]
        return frames


def auth_start(device_apikey: str, app_code: str | None = None) -> tuple[str, bytes]:
    app_code = app_code or str(uuid4())
    return app_code, frame(0, encrypt(app_code, device_apikey))


def auth_token(device_apikey: str, app_code: str, encrypted_device_code: bytes) -> bytes:
    device_code = decrypt(encrypted_device_code, device_apikey)
    if not device_code:
        raise ProtocolError("Empty BLE authentication challenge")
    return frame(2, encrypt(f"{app_code}_{device_code}", device_apikey))


def request(
    device_id: str,
    device_apikey: str,
    account_apikey: str,
    params: dict[str, Any] | list[Any],
    sequence: str | None = None,
    action: str = "update",
) -> tuple[str, bytes]:
    sequence = sequence or str(time.time_ns() // 1_000_000)
    message = {
        "sequence": sequence,
        "userAgent": "app",
        "action": action,
        "deviceid": device_id,
        "apikey": device_apikey,
        "selfApikey": account_apikey,
        "params": params,
    }
    plaintext = json.dumps(message, separators=(",", ":"), ensure_ascii=False)
    return sequence, frame(15, encrypt(plaintext, device_apikey))


def light_params(
    on: bool, brightness: int | None = None, rgb: tuple[int, int, int] | None = None
) -> dict[str, Any]:
    params: dict[str, Any] = {"switch": "on" if on else "off"}
    if not on:
        return params
    if brightness is not None:
        if not 1 <= brightness <= 255:
            raise ValueError("Brightness must be 1-255; use off for zero")
        params.update(mode=0, bright=max(1, round(brightness * 100 / 255)))
    if rgb is not None:
        if len(rgb) != 3 or any(not 0 <= c <= 255 for c in rgb):
            raise ValueError("RGB channels must be 0-255")
        params.update(mode=0, colorR=rgb[0], colorG=rgb[1], colorB=rgb[2])
    return params


def update(
    device_id: str,
    device_apikey: str,
    account_apikey: str,
    params: dict[str, Any],
    sequence: str | None = None,
) -> tuple[str, bytes]:
    return request(device_id, device_apikey, account_apikey, params, sequence)
