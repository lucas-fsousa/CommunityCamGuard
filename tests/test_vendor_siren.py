from __future__ import annotations

import struct

import pytest

from backend.app.db.p2p import P2PEnrollment
from backend.app.drivers.yoosee.p2p import siren
from backend.app.drivers.yoosee.p2p.contracts import (
    CertifiedNode,
    ModelReadResult,
    OnlineDevice,
    P2PProbeError,
)
from backend.app.drivers.yoosee.p2p.crypto import gute_mode2_decrypt


def _enrollment() -> P2PEnrollment:
    return P2PEnrollment("7000000002", 123, bytes(range(64)), None, "now", "now")


def test_siren_builder_is_fixed_to_recovered_expel_action():
    node = CertifiedNode(("192.0.2.10", 19800), 9, bytes(range(32)), 17)
    plain = gute_mode2_decrypt(
        siren.build_siren_action(node, 123, 7000000002, True, 18, 19),
        node.session_key,
    )

    assert plain[:2] == b"\x7e\xac"
    assert struct.unpack_from("<Q", plain, 0x18)[0] == 123
    assert struct.unpack_from("<I", plain, 0x20)[0] == 19
    assert plain[0x26] == 3
    path_length = plain[0x27]
    value_length = struct.unpack_from("<H", plain, 0x28)[0]
    cursor = 0x32
    assert plain[cursor : cursor + path_length].decode() == siren.SIREN_ACTION_PATH
    cursor += path_length + 1
    assert plain[cursor : cursor + value_length] == b"2"


def test_siren_response_and_state_parsers_are_strict():
    response = bytearray(0x36)
    response[1] = 0xAD
    struct.pack_into("<I", response, 0x30, 19)
    struct.pack_into("<H", response, 0x34, 0)

    assert siren.parse_siren_action_response(bytes(response), 19) == 0
    assert siren.parse_siren_action_response(bytes(response), 20) is None
    assert siren.extract_siren_state({"stVal": 1, "t": 123}) == siren.SIREN_OFF
    assert siren.extract_siren_state({"nested": {"stVal": 2}}) == siren.SIREN_ON
    assert siren.extract_siren_state({"stVal": 0}) is None
    assert siren.extract_siren_state(True) is None


def test_bounded_siren_pulse_uses_single_on_and_unconditional_off(monkeypatch):
    enrollment = _enrollment()
    node = CertifiedNode(("192.0.2.10", 19800), 9, bytes(32), 17)
    target = OnlineDevice(7000000002, 1, False, 1, bytes(16))
    calls = []

    class FakeSocket:
        def bind(self, address):
            calls.append(("bind", address))

        def close(self):
            calls.append(("close",))

    monkeypatch.setattr(siren.socket, "socket", lambda *_args, **_kwargs: FakeSocket())
    monkeypatch.setattr(siren, "open_camera_session", lambda *_args: (node, target, 40))
    reads = iter(
        (
            ModelReadResult(True, 0, {"stVal": 1}),
            ModelReadResult(True, 0, {"stVal": 1}),
        )
    )
    monkeypatch.setattr(siren, "exchange_model_read", lambda *_args, **_kwargs: next(reads))

    def fake_action(_sock, _node, access_id, device, enabled, sequence, _timeout, **kwargs):
        calls.append(("action", access_id, device.device_id, enabled, sequence, kwargs["retries"]))
        return siren.SirenActionExchange(True, 0)

    monkeypatch.setattr(siren, "exchange_siren_action", fake_action)
    monkeypatch.setattr(siren.time, "sleep", lambda seconds: calls.append(("sleep", seconds)))

    result = siren.pulse_camera_siren(enrollment, 2)

    assert calls == [
        ("bind", ("", 0)),
        ("action", 123, 7000000002, True, 56, 1),
        ("sleep", 2),
        ("action", 123, 7000000002, False, 72, 3),
        ("close",),
    ]
    assert result.duration_seconds == 2
    assert result.enable_error_code == result.disable_error_code == 0
    assert result.final_off_confirmed is True


def test_siren_preflight_refuses_to_send_when_off_is_not_confirmed(monkeypatch):
    enrollment = _enrollment()
    node = CertifiedNode(("192.0.2.10", 19800), 9, bytes(32), 17)
    target = OnlineDevice(7000000002, 1, False, 1, bytes(16))

    class FakeSocket:
        def bind(self, _address):
            pass

        def close(self):
            pass

    monkeypatch.setattr(siren.socket, "socket", lambda *_args, **_kwargs: FakeSocket())
    monkeypatch.setattr(siren, "open_camera_session", lambda *_args: (node, target, 40))
    monkeypatch.setattr(
        siren,
        "exchange_model_read",
        lambda *_args, **_kwargs: ModelReadResult(True, 0, {"stVal": 2}),
    )
    monkeypatch.setattr(
        siren,
        "exchange_siren_action",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("action sent")),
    )

    with pytest.raises(P2PProbeError, match="confirmed OFF preflight"):
        siren.pulse_camera_siren(enrollment, 2)


def test_failed_siren_enable_still_sends_off(monkeypatch):
    enrollment = _enrollment()
    node = CertifiedNode(("192.0.2.10", 19800), 9, bytes(32), 17)
    target = OnlineDevice(7000000002, 1, False, 1, bytes(16))
    actions = []

    class FakeSocket:
        def bind(self, _address):
            pass

        def close(self):
            pass

    monkeypatch.setattr(siren.socket, "socket", lambda *_args, **_kwargs: FakeSocket())
    monkeypatch.setattr(siren, "open_camera_session", lambda *_args: (node, target, 40))
    monkeypatch.setattr(
        siren,
        "exchange_model_read",
        lambda *_args, **_kwargs: ModelReadResult(True, 0, {"stVal": 1}),
    )

    def fake_action(*_args, **_kwargs):
        enabled = _args[4]
        actions.append(enabled)
        return siren.SirenActionExchange(True, 7 if enabled else 0)

    monkeypatch.setattr(siren, "exchange_siren_action", fake_action)

    with pytest.raises(P2PProbeError, match="activation"):
        siren.pulse_camera_siren(enrollment, 2)

    assert actions == [True, False]


@pytest.mark.parametrize("duration", [True, 0, 11, 1.5])
def test_siren_duration_is_rejected_before_network(monkeypatch, duration):
    monkeypatch.setattr(
        siren.socket,
        "socket",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("network opened")),
    )

    with pytest.raises(ValueError, match="1 to 10 seconds"):
        siren.pulse_camera_siren(_enrollment(), duration)  # type: ignore[arg-type]
