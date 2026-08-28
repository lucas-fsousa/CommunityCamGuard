from __future__ import annotations

import hashlib
import socket
import struct

import pytest

from backend.app.db.p2p import P2PEnrollment
from backend.app.vendor_p2p import client, rtsp_setup
from backend.app.vendor_p2p.crypto import gute_mode2_decrypt


def _enrollment() -> P2PEnrollment:
    return P2PEnrollment(
        "7000000002", 123, bytes(range(64)), None, "now", "now"
    )


class FakeSocket:
    def __init__(self):
        self.closed = False

    def bind(self, _address):
        pass

    def close(self):
        self.closed = True


def test_rtsp_password_digest_matches_recovered_apk_contract():
    password = "SafePass123"
    expected = hashlib.md5(
        f"admin:HIipCamera:{password}".encode(), usedforsecurity=False
    ).hexdigest()

    assert rtsp_setup.rtsp_password_digest(password) == expected
    assert password not in expected


@pytest.mark.parametrize("password", ["short", "contains-symbol!", "x" * 31])
def test_rtsp_password_rejects_values_outside_apk_contract(password):
    with pytest.raises(ValueError):
        rtsp_setup.rtsp_password_digest(password)


def test_onvif_builder_is_fixed_to_boolean_property():
    node = client.CertifiedNode(("192.0.2.10", 19800), 9, bytes(range(32)), 17)
    wire = rtsp_setup.build_onvif_enable_write(node, 7000000002, True, 18, 19)
    plain = gute_mode2_decrypt(wire, node.session_key)

    assert plain[:2] == b"\x7e\xd2"
    assert struct.unpack_from("<Q", plain, 0x2A)[0] == 7000000002
    path_length = plain[0x27]
    value_length = struct.unpack_from("<H", plain, 0x28)[0]
    cursor = 0x32
    assert plain[cursor : cursor + path_length].decode() == rtsp_setup.ONVIF_WRITE_PATH
    cursor += path_length + 1
    assert plain[cursor : cursor + value_length] == b"1"
    with pytest.raises(ValueError, match="boolean"):
        rtsp_setup.build_onvif_enable_write(node, 7000000002, 1, 18, 19)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (0, False),
        ({"setVal": 1, "t": 123}, True),
        ({"wrapper": {"value": 0}}, False),
        ({"setVal": 2}, None),
        (True, None),
    ],
)
def test_extract_onvif_enabled_is_bounded(value, expected):
    assert rtsp_setup.extract_onvif_enabled(value) is expected


def test_prepare_rtsp_uses_preflight_and_single_password_delivery(monkeypatch):
    fake_socket = FakeSocket()
    node = client.CertifiedNode(("192.0.2.10", 19800), 1, bytes(32), 2)
    target = client.OnlineDevice(7000000002, 1, False, 1, bytes(16))
    calls = []
    monkeypatch.setattr(socket, "socket", lambda *_args, **_kwargs: fake_socket)
    monkeypatch.setattr(
        client, "_camera_session", lambda *_args: (node, target, 40)
    )
    monkeypatch.setattr(
        rtsp_setup,
        "_set_onvif_in_session",
        lambda *_args: (True, None),
    )

    def fake_password(_sock, _node, enrollment, device, password, sequence, *_args):
        calls.append((enrollment.device_id, device.device_id, password, sequence))
        return rtsp_setup._PasswordExchange(True, False, None)

    monkeypatch.setattr(rtsp_setup, "_exchange_password", fake_password)

    result = rtsp_setup.prepare_camera_rtsp(_enrollment(), "SafePass123")

    assert calls == [("7000000002", 7000000002, "SafePass123", 72)]
    assert result.previous_enabled is True
    assert result.enabled_changed is False
    assert result.password_delivery_acknowledged is True
    assert result.password_response_accepted is False
    assert fake_socket.closed is True


def test_unacknowledged_password_restores_initial_disabled_state(monkeypatch):
    fake_socket = FakeSocket()
    node = client.CertifiedNode(("192.0.2.10", 19800), 1, bytes(32), 2)
    target = client.OnlineDevice(7000000002, 1, False, 1, bytes(16))
    states = []
    monkeypatch.setattr(socket, "socket", lambda *_args, **_kwargs: fake_socket)
    monkeypatch.setattr(client, "_camera_session", lambda *_args: (node, target, 40))

    def fake_set(*args):
        enabled = args[3]
        states.append(enabled)
        return (not enabled, client.ModelWriteResult(True, 0))

    monkeypatch.setattr(rtsp_setup, "_set_onvif_in_session", fake_set)
    monkeypatch.setattr(
        rtsp_setup,
        "_exchange_password",
        lambda *_args: rtsp_setup._PasswordExchange(False, False, None),
    )

    with pytest.raises(client.P2PProbeError, match="not acknowledged"):
        rtsp_setup.prepare_camera_rtsp(_enrollment(), "SafePass123")

    assert states == [True, False]
    assert fake_socket.closed is True


def test_invalid_password_is_rejected_before_network(monkeypatch):
    monkeypatch.setattr(
        socket,
        "socket",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("network opened")),
    )
    with pytest.raises(ValueError):
        rtsp_setup.prepare_camera_rtsp(_enrollment(), "bad!")
