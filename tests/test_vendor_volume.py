from __future__ import annotations

import struct

import pytest

from backend.app.db.p2p import P2PEnrollment
from backend.app.drivers.yoosee.p2p import volume
from backend.app.drivers.yoosee.p2p.contracts import (
    CertifiedNode,
    ModelReadResult,
    ModelWriteResult,
    OnlineDevice,
)
from backend.app.drivers.yoosee.p2p.crypto import gute_mode2_decrypt


def _enrollment() -> P2PEnrollment:
    return P2PEnrollment("7000000002", 123, bytes(range(64)), None, "now", "now")


def test_volume_builder_uses_only_the_five_apk_positions():
    node = CertifiedNode(("192.0.2.10", 19800), 9, bytes(range(32)), 17)
    plain = gute_mode2_decrypt(
        volume.build_volume_write(node, 7000000002, 75, 18, 19), node.session_key
    )

    assert plain[:2] == b"\x7e\xd2"
    assert plain[0x26] == 7
    path_length = plain[0x27]
    value_length = struct.unpack_from("<H", plain, 0x28)[0]
    cursor = 0x32
    assert plain[cursor : cursor + path_length].decode() == volume.VOLUME_WRITE_PATH
    cursor += path_length + 1
    assert plain[cursor : cursor + value_length] == b"7"


@pytest.mark.parametrize(
    ("raw", "percent"),
    [(0, 0), (1, 25), (2, 25), (3, 50), (5, 50), (6, 75), (7, 75), (8, 100), (10, 100)],
)
def test_volume_buckets_match_the_apk(raw, percent):
    assert volume.volume_percent(raw) == percent
    assert volume.extract_volume_raw({"setVal": raw, "t": 1788000000}) == raw


def test_volume_change_requires_preflight_d3_and_exact_readback(monkeypatch):
    enrollment = _enrollment()
    node = CertifiedNode(("192.0.2.10", 19800), 9, bytes(32), 17)
    target = OnlineDevice(7000000002, 1, False, 1, bytes(16))
    calls = []

    class FakeSocket:
        def bind(self, address):
            calls.append(("bind", address))

        def close(self):
            calls.append(("close",))

    monkeypatch.setattr(volume.socket, "socket", lambda *_args, **_kwargs: FakeSocket())
    monkeypatch.setattr(volume, "open_camera_session", lambda *_args: (node, target, 40))
    reads = iter(
        (
            ModelReadResult(True, 0, {"setVal": 10}),
            ModelReadResult(True, 0, {"setVal": 10}),
            ModelReadResult(True, 0, {"setVal": 7}),
        )
    )

    def fake_read(_sock, _node, _device, _path, sequence, _timeout, **_kwargs):
        calls.append(("read", sequence))
        return next(reads)

    def fake_write(_sock, _node, _device, selected, sequence, _timeout, **_kwargs):
        calls.append(("write", selected, sequence))
        return ModelWriteResult(True, 0)

    monkeypatch.setattr(volume, "exchange_model_read", fake_read)
    monkeypatch.setattr(volume, "exchange_volume_write", fake_write)
    monkeypatch.setattr(volume.time, "sleep", lambda _seconds: None)

    result = volume.set_camera_speaker_volume(enrollment, 75)

    assert calls == [
        ("bind", ("", 0)),
        ("read", 40),
        ("write", 75, 41),
        ("read", 42),
        ("read", 43),
        ("close",),
    ]
    assert result.previous_percent == 100
    assert result.previous_raw == 10
    assert result.requested_raw == 7
    assert result.verified is True


def test_volume_read_returns_normalized_percent_and_preserves_raw_value(monkeypatch):
    enrollment = _enrollment()
    node = CertifiedNode(("192.0.2.10", 19800), 9, bytes(32), 17)
    target = OnlineDevice(7000000002, 1, False, 1, bytes(16))

    class FakeSocket:
        def bind(self, _address):
            pass

        def close(self):
            pass

    monkeypatch.setattr(volume.socket, "socket", lambda *_args, **_kwargs: FakeSocket())
    monkeypatch.setattr(volume, "open_camera_session", lambda *_args: (node, target, 40))
    monkeypatch.setattr(
        volume,
        "exchange_model_read",
        lambda *_args, **_kwargs: ModelReadResult(True, 0, {"setVal": 6}),
    )

    result = volume.read_camera_speaker_volume(enrollment)

    assert result.volume_percent == 75
    assert result.raw_value == 6
    assert result.authenticated is True
    assert result.direct_handshake is False


def test_volume_is_idempotent_within_the_same_apk_bucket(monkeypatch):
    enrollment = _enrollment()
    node = CertifiedNode(("192.0.2.10", 19800), 9, bytes(32), 17)
    target = OnlineDevice(7000000002, 1, False, 1, bytes(16))

    class FakeSocket:
        def bind(self, _address):
            pass

        def close(self):
            pass

    monkeypatch.setattr(volume.socket, "socket", lambda *_args, **_kwargs: FakeSocket())
    monkeypatch.setattr(volume, "open_camera_session", lambda *_args: (node, target, 40))
    monkeypatch.setattr(
        volume,
        "exchange_model_read",
        lambda *_args, **_kwargs: ModelReadResult(True, 0, {"setVal": 8}),
    )
    monkeypatch.setattr(
        volume,
        "exchange_volume_write",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("write sent")),
    )

    result = volume.set_camera_speaker_volume(enrollment, 100)

    assert result.changed is False
    assert result.previous_raw == 8
    assert result.verified is True


@pytest.mark.parametrize("selected", [True, -1, 1, 20, 101, "100"])
def test_volume_rejects_unknown_positions_before_network(monkeypatch, selected):
    monkeypatch.setattr(
        volume.socket,
        "socket",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("network opened")),
    )

    with pytest.raises(ValueError, match="0, 25, 50, 75 or 100"):
        volume.set_camera_speaker_volume(_enrollment(), selected)  # type: ignore[arg-type]
