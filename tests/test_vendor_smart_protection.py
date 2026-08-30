from __future__ import annotations

import struct

import pytest

from backend.app.db.p2p import P2PEnrollment
from backend.app.drivers.yoosee.p2p import smart_protection
from backend.app.drivers.yoosee.p2p.contracts import (
    CertifiedNode,
    ModelReadResult,
    ModelWriteResult,
    OnlineDevice,
)
from backend.app.drivers.yoosee.p2p.crypto import gute_mode2_decrypt


def _enrollment() -> P2PEnrollment:
    return P2PEnrollment("7000000002", 123, bytes(range(64)), None, "now", "now")


def test_smart_protection_builder_targets_only_the_guard_master_leaf():
    node = CertifiedNode(("192.0.2.10", 19800), 9, bytes(range(32)), 17)
    plain = gute_mode2_decrypt(
        smart_protection.build_smart_protection_write(node, 7000000002, False, 18, 19),
        node.session_key,
    )

    path_length = plain[0x27]
    value_length = struct.unpack_from("<H", plain, 0x28)[0]
    cursor = 0x32
    assert (
        plain[cursor : cursor + path_length].decode()
        == smart_protection.SMART_PROTECTION_WRITE_PATH
    )
    cursor += path_length + 1
    assert plain[cursor : cursor + value_length] == b"0"


def test_smart_protection_extractor_does_not_pick_an_unrelated_enable():
    assert (
        smart_protection.extract_smart_protection_enabled(
            {"setVal": {"enable": 1, "plan": {"enable": 0}}, "t": 1788000000}
        )
        is True
    )
    assert smart_protection.extract_smart_protection_enabled({"plan": {"enable": 1}}) is None


def test_smart_protection_change_requires_preflight_and_readback(monkeypatch):
    enrollment = _enrollment()
    node = CertifiedNode(("192.0.2.10", 19800), 9, bytes(32), 17)
    target = OnlineDevice(7000000002, 1, False, 1, bytes(16))
    calls = []

    class FakeSocket:
        def bind(self, address):
            calls.append(("bind", address))

        def close(self):
            calls.append(("close",))

    monkeypatch.setattr(smart_protection.socket, "socket", lambda *_args, **_kwargs: FakeSocket())
    monkeypatch.setattr(smart_protection, "open_camera_session", lambda *_args: (node, target, 40))
    reads = iter(
        (
            ModelReadResult(True, 0, {"setVal": {"enable": 1}}),
            ModelReadResult(True, 0, {"setVal": {"enable": 1}}),
            ModelReadResult(True, 0, {"setVal": {"enable": 0}}),
        )
    )

    def fake_read(_sock, _node, _device, _path, sequence, _timeout, **_kwargs):
        calls.append(("read", sequence))
        return next(reads)

    def fake_write(_sock, _node, _device, enabled, sequence, _timeout, **_kwargs):
        calls.append(("write", enabled, sequence))
        return ModelWriteResult(True, 0)

    monkeypatch.setattr(smart_protection, "exchange_model_read", fake_read)
    monkeypatch.setattr(smart_protection, "exchange_smart_protection_write", fake_write)
    monkeypatch.setattr(smart_protection.time, "sleep", lambda _seconds: None)

    result = smart_protection.set_camera_smart_protection(enrollment, False)

    assert calls == [
        ("bind", ("", 0)),
        ("read", 40),
        ("write", False, 41),
        ("read", 42),
        ("read", 43),
        ("close",),
    ]
    assert result.previous_enabled is True
    assert result.enabled is False
    assert result.verified is True


def test_smart_protection_read_is_explicit_and_typed(monkeypatch):
    enrollment = _enrollment()
    node = CertifiedNode(("192.0.2.10", 19800), 9, bytes(32), 17)
    target = OnlineDevice(7000000002, 1, False, 1, bytes(16))

    class FakeSocket:
        def bind(self, _address):
            pass

        def close(self):
            pass

    monkeypatch.setattr(smart_protection.socket, "socket", lambda *_args, **_kwargs: FakeSocket())
    monkeypatch.setattr(smart_protection, "open_camera_session", lambda *_args: (node, target, 40))
    monkeypatch.setattr(
        smart_protection,
        "exchange_model_read",
        lambda *_args, **_kwargs: ModelReadResult(True, 0, {"setVal": {"enable": 1}}),
    )

    result = smart_protection.read_camera_smart_protection(enrollment)

    assert result.enabled is True
    assert result.authenticated is True
    assert result.direct_handshake is False


@pytest.mark.parametrize("selected", [0, 1, "true", None])
def test_smart_protection_rejects_non_boolean_before_network(monkeypatch, selected):
    monkeypatch.setattr(
        smart_protection.socket,
        "socket",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("network opened")),
    )

    with pytest.raises(ValueError, match="boolean"):
        smart_protection.set_camera_smart_protection(  # type: ignore[arg-type]
            _enrollment(), selected
        )
