from __future__ import annotations

import struct

import pytest

from backend.app.db.p2p import P2PEnrollment
from backend.app.drivers.yoosee.p2p import night_vision
from backend.app.drivers.yoosee.p2p.contracts import (
    CertifiedNode,
    ModelReadResult,
    ModelWriteResult,
    OnlineDevice,
)
from backend.app.drivers.yoosee.p2p.crypto import gute_mode2_decrypt


def _enrollment() -> P2PEnrollment:
    return P2PEnrollment("7000000002", 123, bytes(range(64)), None, "now", "now")


def test_night_vision_builder_uses_only_the_proven_legacy_leaf():
    node = CertifiedNode(("192.0.2.10", 19800), 9, bytes(range(32)), 17)
    plain = gute_mode2_decrypt(
        night_vision.build_night_vision_write(node, 7000000002, "night", 18, 19),
        node.session_key,
    )

    assert plain[:2] == b"\x7e\xd2"
    assert plain[0x26] == 7
    path_length = plain[0x27]
    value_length = struct.unpack_from("<H", plain, 0x28)[0]
    cursor = 0x32
    assert plain[cursor : cursor + path_length].decode() == night_vision.NIGHT_VISION_WRITE_PATH
    cursor += path_length + 1
    assert plain[cursor : cursor + value_length] == b"2"
    assert b"nightViewModeV2" not in plain


@pytest.mark.parametrize("mode", [0, 1, 2])
def test_extract_night_vision_mode_accepts_only_legacy_states(mode):
    assert (
        night_vision.extract_night_vision_mode(
            {"setVal": {"nightViewMode": mode, "videoLevel": 4}, "t": 1788000000}
        )
        == mode
    )


@pytest.mark.parametrize(
    "value",
    [True, -1, 3, {"nightViewModeV2": {"setVal": {"enable": 2}}}],
)
def test_extract_night_vision_mode_rejects_unrelated_or_unknown_values(value):
    assert night_vision.extract_night_vision_mode(value) is None


def test_night_vision_change_requires_preflight_d3_and_exact_readback(monkeypatch):
    enrollment = _enrollment()
    node = CertifiedNode(("192.0.2.10", 19800), 9, bytes(32), 17)
    target = OnlineDevice(7000000002, 1, False, 1, bytes(16))
    calls = []

    class FakeSocket:
        def bind(self, address):
            calls.append(("bind", address))

        def close(self):
            calls.append(("close",))

    monkeypatch.setattr(night_vision.socket, "socket", lambda *_args, **_kwargs: FakeSocket())
    monkeypatch.setattr(night_vision, "open_camera_session", lambda *_args: (node, target, 40))
    reads = iter(
        (
            ModelReadResult(True, 0, {"setVal": {"nightViewMode": 0}}),
            ModelReadResult(True, 0, {"setVal": {"nightViewMode": 0}}),
            ModelReadResult(True, 0, {"setVal": {"nightViewMode": 1}}),
        )
    )

    def fake_read(_sock, _node, _device, path, sequence, _timeout, **_kwargs):
        calls.append(("read", path, sequence))
        return next(reads)

    def fake_write(_sock, _node, _device, selected, sequence, _timeout, **_kwargs):
        calls.append(("write", selected, sequence))
        return ModelWriteResult(True, 0)

    monkeypatch.setattr(night_vision, "exchange_model_read", fake_read)
    monkeypatch.setattr(night_vision, "exchange_night_vision_write", fake_write)
    monkeypatch.setattr(night_vision.time, "sleep", lambda _seconds: None)

    result = night_vision.set_camera_night_vision(enrollment, "daytime")

    assert calls == [
        ("bind", ("", 0)),
        ("read", night_vision.NIGHT_VISION_READ_PATH, 40),
        ("write", "daytime", 41),
        ("read", night_vision.NIGHT_VISION_READ_PATH, 42),
        ("read", night_vision.NIGHT_VISION_READ_PATH, 43),
        ("close",),
    ]
    assert result.previous_value == 0
    assert result.requested_value == 1
    assert result.changed is True
    assert result.verified is True


def test_night_vision_is_idempotent(monkeypatch):
    enrollment = _enrollment()
    node = CertifiedNode(("192.0.2.10", 19800), 9, bytes(32), 17)
    target = OnlineDevice(7000000002, 1, False, 1, bytes(16))

    class FakeSocket:
        def bind(self, _address):
            pass

        def close(self):
            pass

    monkeypatch.setattr(night_vision.socket, "socket", lambda *_args, **_kwargs: FakeSocket())
    monkeypatch.setattr(night_vision, "open_camera_session", lambda *_args: (node, target, 40))
    monkeypatch.setattr(
        night_vision,
        "exchange_model_read",
        lambda *_args, **_kwargs: ModelReadResult(True, 0, {"setVal": {"nightViewMode": 0}}),
    )
    monkeypatch.setattr(
        night_vision,
        "exchange_night_vision_write",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("write sent")),
    )

    result = night_vision.set_camera_night_vision(enrollment, "automatic")

    assert result.changed is False
    assert result.verified is True


@pytest.mark.parametrize("selected", [True, "auto", "day", "ir", 0, None])
def test_night_vision_rejects_unknown_modes_before_network(monkeypatch, selected):
    monkeypatch.setattr(
        night_vision.socket,
        "socket",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("network opened")),
    )

    with pytest.raises(ValueError, match="automatic, daytime or night"):
        night_vision.set_camera_night_vision(_enrollment(), selected)  # type: ignore[arg-type]
