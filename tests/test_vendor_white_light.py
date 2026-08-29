from __future__ import annotations

import json
import struct

import pytest

from backend.app.db.p2p import P2PEnrollment
from backend.app.drivers.yoosee.p2p import client as transport
from backend.app.drivers.yoosee.p2p import white_light
from backend.app.drivers.yoosee.p2p.crypto import gute_mode2_decrypt


def _enrollment() -> P2PEnrollment:
    return P2PEnrollment(
        device_id="7000000002",
        access_id=123,
        access_token=bytes(range(64)),
        dev_token=None,
        created_at="now",
        updated_at="now",
    )


def test_builder_exposes_only_typed_read_and_boolean_write():
    node = transport.CertifiedNode(("192.0.2.10", 19800), 9, bytes(range(32)), 17)

    read = gute_mode2_decrypt(
        white_light.build_white_light_request(node, 123, 7000000002, None, 18, 19, 20),
        node.session_key,
    )
    write = gute_mode2_decrypt(
        white_light.build_white_light_request(node, 123, 7000000002, True, 21, 22, 23),
        node.session_key,
    )

    assert read[:2] == b"\x7e\xb9"
    assert struct.unpack_from("<Q", read, 0x1C)[0] == 7000000002
    assert struct.unpack_from("<Q", read, 0x24)[0] == 123
    assert read[0x34:0x3C] == b"\x01\xff\x00\x00" + struct.pack("<I", 20)
    assert json.loads(read[0x3C:].decode()) == {"type": 12}
    assert json.loads(write[0x3C:].decode()) == {
        "data": {"whiteLightCtrl": 1, "whiteLightStatus": 0},
        "type": 11,
    }
    with pytest.raises(ValueError, match="boolean"):
        white_light.build_white_light_request(node, 123, 7000000002, 1, 18, 19, 20)
    assert not hasattr(white_light, "build_passthrough_message")


def test_response_requires_exact_type_and_binary_state():
    encoded = b'{"type":12,"data":{"whiteLightStatus":1}}'
    payload = b"\x01\xff\x00\x00" + struct.pack("<I", 44) + encoded
    response = bytearray(0x34 + len(payload))
    response[:2] = b"\x7e\xb9"
    struct.pack_into("<H", response, 0x30, len(payload))
    response[0x34:] = payload

    parsed = white_light.parse_white_light_response(bytes(response), 12)
    assert parsed == (44, {"type": 12, "data": {"whiteLightStatus": 1}})
    assert white_light.parse_white_light_response(bytes(response), 11) is None
    assert white_light.extract_white_light_state(parsed[1]) is True
    assert (
        white_light.extract_white_light_state({"type": 12, "data": {"whiteLightStatus": 2}}) is None
    )


def test_change_requires_preflight_acceptance_and_fresh_readback(monkeypatch):
    enrollment = _enrollment()
    node = transport.CertifiedNode(("192.0.2.10", 19800), 1, bytes(32), 2)
    target = transport.OnlineDevice(7000000002, 1, False, 1, bytes(16))
    calls = []

    class FakeSocket:
        def bind(self, address):
            calls.append(("bind", address))

        def close(self):
            calls.append(("close",))

    monkeypatch.setattr(white_light.socket, "socket", lambda *_args, **_kwargs: FakeSocket())
    monkeypatch.setattr(white_light, "open_camera_session", lambda *_args: (node, target, 40))
    replies = iter(
        (
            white_light.WhiteLightExchange(
                True, True, {"type": 12, "data": {"whiteLightStatus": 0}}
            ),
            white_light.WhiteLightExchange(True, True, {"type": 11, "err": 0}),
            white_light.WhiteLightExchange(
                True, True, {"type": 12, "data": {"whiteLightStatus": 0}}
            ),
            white_light.WhiteLightExchange(
                True, True, {"type": 12, "data": {"whiteLightStatus": 1}}
            ),
        )
    )

    def fake_exchange(_sock, _node, access_id, device, enabled, sequence, _timeout, **kwargs):
        calls.append(
            (
                "exchange",
                access_id,
                device.device_id,
                enabled,
                sequence,
                kwargs.get("retries"),
            )
        )
        return next(replies)

    monkeypatch.setattr(white_light, "exchange_white_light", fake_exchange)
    monkeypatch.setattr(white_light.time, "sleep", lambda _seconds: None)

    result = white_light.set_camera_white_light(enrollment, True)

    assert calls == [
        ("bind", ("", 0)),
        ("exchange", 123, 7000000002, None, 40, None),
        ("exchange", 123, 7000000002, True, 41, 1),
        ("exchange", 123, 7000000002, None, 42, 1),
        ("exchange", 123, 7000000002, None, 43, 1),
        ("close",),
    ]
    assert result.previous_enabled is False
    assert result.enabled is True
    assert result.changed is True
    assert result.verified is True


def test_change_is_idempotent_and_invalid_values_open_no_network(monkeypatch):
    enrollment = _enrollment()
    node = transport.CertifiedNode(("192.0.2.10", 19800), 1, bytes(32), 2)
    target = transport.OnlineDevice(7000000002, 1, False, 1, bytes(16))

    class FakeSocket:
        def bind(self, _address):
            pass

        def close(self):
            pass

    monkeypatch.setattr(white_light.socket, "socket", lambda *_args, **_kwargs: FakeSocket())
    monkeypatch.setattr(white_light, "open_camera_session", lambda *_args: (node, target, 8))
    monkeypatch.setattr(
        white_light,
        "exchange_white_light",
        lambda *_args, **_kwargs: white_light.WhiteLightExchange(
            True, True, {"type": 12, "data": {"whiteLightStatus": 0}}
        ),
    )

    result = white_light.set_camera_white_light(enrollment, False)
    assert result.changed is False
    assert result.previous_enabled is result.enabled is False

    monkeypatch.setattr(
        white_light.socket,
        "socket",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("network opened")),
    )
    with pytest.raises(ValueError, match="boolean"):
        white_light.set_camera_white_light(enrollment, 1)
