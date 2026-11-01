import asyncio
import json
import struct
import subprocess
from types import SimpleNamespace

import pytest

from custom_components.sonoff_l2_ble import protocol as p
from custom_components.sonoff_l2_ble.transport import L2Transport

KEY = "11111111-2222-3333-4444-555555555555"  # Synthetic credentials only.


def test_crypto_matches_independent_node_implementation():
    script = """
const crypto = require('crypto');
const key=crypto.createHash('md5').update(process.argv[1]).digest();
const c=crypto.createCipheriv('aes-128-cbc',key,Buffer.from('0000000000000000'));
process.stdout.write(Buffer.concat([c.update('app-code_device-code','utf8'),c.final()]).toString('base64'));
"""
    expected = subprocess.check_output(["node", "-e", script, KEY])
    assert p.encrypt("app-code_device-code", KEY) == expected
    assert p.decrypt(expected, KEY) == "app-code_device-code"


@pytest.mark.parametrize("chunk_size", [1, 2, 3, 4, 20, 244, 2048])
def test_split_and_coalesced_frames(chunk_size):
    original = [(1, b"a" * 260), (3, b"\0"), (16, b"hello")]
    stream = b"".join(p.frame(cmd, data) for cmd, data in original)
    buffer = p.FrameBuffer()
    result = []
    for offset in range(0, len(stream), chunk_size):
        result += buffer.feed(stream[offset : offset + chunk_size])
    assert result == original
    assert not buffer.data


def test_auth_and_update_match_apk_layout():
    app, packet = p.auth_start(KEY, "fixed-app-code")
    assert struct.unpack("<HH", packet[:4]) == (0, len(packet) - 4)
    assert p.decrypt(packet[4:], KEY) == app
    token = p.auth_token(KEY, app, p.encrypt("device-code", KEY))
    assert struct.unpack("<HH", token[:4]) == (2, len(token) - 4)
    assert p.decrypt(token[4:], KEY) == "fixed-app-code_device-code"
    seq, packet = p.update("1000000000", KEY, "account-key", {"switch": "on"}, "123")
    assert seq == "123" and packet[:2] == b"\x0f\0"
    message = json.loads(p.decrypt(packet[4:], KEY))
    assert message == {
        "sequence": "123",
        "userAgent": "app",
        "action": "update",
        "deviceid": "1000000000",
        "apikey": KEY,
        "selfApikey": "account-key",
        "params": {"switch": "on"},
    }


def test_invalid_frame_and_wrong_key():
    with pytest.raises(p.ProtocolError):
        p.FrameBuffer().feed(struct.pack("<HH", 16, 65535))
    with pytest.raises(p.ProtocolError):
        p.decrypt(b"not base64!", KEY)
    with pytest.raises(p.ProtocolError):
        p.decrypt(p.encrypt("test", KEY), "wrong-key")


def test_rgb_and_brightness_payloads():
    assert p.light_params(False) == {"switch": "off"}
    assert p.light_params(True) == {"switch": "on"}
    assert p.light_params(True, 128, (255, 0, 7)) == {
        "switch": "on",
        "mode": 0,
        "bright": 50,
        "colorR": 255,
        "colorG": 0,
        "colorB": 7,
    }
    assert p.light_params(True, 1)["bright"] == 1
    assert p.light_params(True, 255)["bright"] == 100
    with pytest.raises(ValueError):
        p.light_params(True, 0)
    with pytest.raises(ValueError):
        p.light_params(True, rgb=(256, 0, 0))


class FakeL2:
    """Simulated peer: exercise fragmentation, unsolicited updates and ACK gates.

    This is a software regression probe, not evidence of physical L2 compatibility.
    """

    def __init__(self, reject_auth=False, error=0, malformed=False):
        self.reject_auth, self.error, self.malformed = reject_auth, error, malformed
        self.disconnected = False
        self.buffer = p.FrameBuffer()
        self.commands = []
        self.characteristic = SimpleNamespace(properties=["write"])
        self.services = SimpleNamespace(get_service=lambda uuid: self)

    def get_characteristic(self, uuid):
        return self.characteristic

    async def start_notify(self, uuid, callback):
        assert uuid == p.NOTIFY
        self.callback = callback

    def notify(self, command, payload):
        packet = p.frame(command, payload)
        for offset in range(0, len(packet), 7):
            self.callback(None, packet[offset : offset + 7])

    async def write_gatt_char(self, characteristic, packet, response):
        assert len(packet) <= 20 and response
        for command, payload in self.buffer.feed(packet):
            self.commands.append(command)
            if command == 0:
                self.app = p.decrypt(payload, KEY)
                self.notify(1, p.encrypt("peer-code", KEY))
            elif command == 2:
                assert p.decrypt(payload, KEY) == self.app + "_peer-code"
                self.notify(3, b"\x01" if self.reject_auth else b"\0")
            elif command == 15:
                self.request = json.loads(p.decrypt(payload, KEY))
                self.notify(
                    16,
                    p.encrypt(json.dumps({"action": "update", "params": {"switch": "off"}}), KEY),
                )
                self.notify(16, p.encrypt(json.dumps({"sequence": "old", "error": 0}), KEY))
                if self.malformed:
                    self.notify(16, p.encrypt("[]", KEY))
                else:
                    reply = {"sequence": self.request["sequence"], "error": self.error}
                    self.notify(16, p.encrypt(json.dumps(reply), KEY))
            else:
                raise AssertionError(command)

    async def disconnect(self):
        self.disconnected = True


def bridge(peer):
    async def factory():
        return peer

    return L2Transport(factory, "1000000000", KEY, "account-key")


@pytest.mark.asyncio
async def test_full_session_waits_for_matching_ack_and_disconnects():
    peer = FakeL2()
    reply = await bridge(peer).send_update({"switch": "on"})
    assert reply["error"] == 0
    assert peer.commands == [0, 2, 15] and peer.disconnected
    assert peer.request["params"] == {"switch": "on"}


@pytest.mark.asyncio
async def test_validation_only_authenticates():
    peer = FakeL2()
    await bridge(peer).authenticate()
    assert peer.commands == [0, 2] and peer.disconnected


@pytest.mark.asyncio
@pytest.mark.parametrize("kwargs", [{"reject_auth": True}, {"error": 400}, {"malformed": True}])
async def test_failures_never_report_success_and_release_connection(kwargs):
    peer = FakeL2(**kwargs)
    with pytest.raises(p.ProtocolError):
        await bridge(peer).send_update({"switch": "on"})
    assert peer.disconnected
    if kwargs.get("reject_auth"):
        assert peer.commands == [0, 2]


@pytest.mark.asyncio
async def test_cancellation_releases_proxy_slot():
    peer = FakeL2()

    async def blocked_write(*args, **kwargs):
        await asyncio.Event().wait()

    peer.write_gatt_char = blocked_write
    task = asyncio.create_task(bridge(peer).authenticate())
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert peer.disconnected


def test_query_envelope_uses_query_not_update():
    sequence, packet = p.request("1000000000", KEY, "account-key", [], "456", action="query")
    message = json.loads(p.decrypt(packet[4:], KEY))
    assert sequence == "456"
    assert message["action"] == "query" and message["params"] == []
    assert packet[:2] == b"\x0f\0"


@pytest.mark.asyncio
async def test_query_ack_does_not_invent_state_readback():
    peer = FakeL2()
    result = await bridge(peer).query()
    assert result["error"] == 0 and "params" not in result
    assert peer.request["action"] == "query" and peer.request["params"] == []


@pytest.mark.parametrize("kind", ["service", "characteristic", "write"])
async def test_missing_gatt_surface_disconnects(kind):
    peer = FakeL2()
    if kind == "service":
        peer.services.get_service = lambda uuid: None
    elif kind == "characteristic":
        peer.get_characteristic = lambda uuid: None
    else:
        peer.characteristic.properties = []
    with pytest.raises(p.ProtocolError):
        await bridge(peer).authenticate()
    assert peer.disconnected


async def test_disconnect_failure_does_not_mask_authentication_error():
    peer = FakeL2(reject_auth=True)

    async def broken_disconnect():
        raise OSError("cleanup error")

    peer.disconnect = broken_disconnect
    with pytest.raises(p.ProtocolError, match="authentication"):
        await bridge(peer).authenticate()


def test_oversized_outgoing_frame_and_empty_auth_challenge():
    with pytest.raises(p.ProtocolError):
        p.frame(15, b"a" * 65536)
    with pytest.raises(p.ProtocolError):
        p.auth_token(KEY, "app", p.encrypt("", KEY))
