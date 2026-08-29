from __future__ import annotations

import base64
import json
import struct

import pytest

from backend.app.db.p2p import P2PEnrollment
from backend.app.drivers.yoosee.p2p import alarm_voice_selection
from backend.app.drivers.yoosee.p2p.alarm_voice import (
    AlarmVoiceResource,
    decode_alarm_voice_catalog,
)
from backend.app.drivers.yoosee.p2p.contracts import (
    CertifiedNode,
    ModelReadResult,
    ModelWriteResult,
    OnlineDevice,
)
from backend.app.drivers.yoosee.p2p.crypto import gute_mode2_decrypt


def _resource_id(number: int, opaque: int = 10) -> str:
    return base64.b64encode(struct.pack("<IIQQ", 4, number, 0, opaque)).decode()


def _resource(number: int = 7) -> AlarmVoiceResource:
    catalog = decode_alarm_voice_catalog(
        json.dumps(
            {
                "code": 0,
                "data": {
                    "total": 1,
                    "urls": [
                        {
                            "resType": 4,
                            "isSys": 1,
                            "resId": _resource_id(number),
                            "desc": {"name": "Latido", "audioFormat": "AMR"},
                        }
                    ],
                },
            }
        ).encode()
    )
    assert catalog is not None
    return catalog.resources[0]


def _enrollment() -> P2PEnrollment:
    return P2PEnrollment("7000000002", 123, bytes(range(64)), None, "now", "now")


def test_selection_builder_accepts_only_catalogue_resource_and_keeps_id_in_wire():
    node = CertifiedNode(("192.0.2.10", 19800), 9, bytes(range(32)), 17)
    resource = _resource()
    plain = gute_mode2_decrypt(
        alarm_voice_selection.build_alarm_voice_selection_write(node, 7000000002, resource, 18, 19),
        node.session_key,
    )

    path_length = plain[0x27]
    value_length = struct.unpack_from("<H", plain, 0x28)[0]
    cursor = 0x32
    assert (
        plain[cursor : cursor + path_length].decode()
        == alarm_voice_selection.ALARM_VOICE_WRITE_PATH
    )
    cursor += path_length + 1
    assert json.loads(plain[cursor : cursor + value_length]) == resource.resource_id

    with pytest.raises(ValueError, match="validated catalogue"):
        alarm_voice_selection.build_alarm_voice_selection_write(  # type: ignore[arg-type]
            node, 7000000002, resource.resource_id, 18, 19
        )


def test_selection_extractor_requires_support_and_type_four_id():
    assert alarm_voice_selection.extract_alarm_voice_selection(
        {"setVal": {"supportFunc": 2, "resId": _resource_id(7)}}
    ) == alarm_voice_selection.AlarmVoiceSelectionState(7, 2)
    assert (
        alarm_voice_selection.extract_alarm_voice_selection(
            {"setVal": {"supportFunc": 0, "resId": _resource_id(7)}}
        )
        is None
    )


def test_selection_requires_preflight_and_logical_readback(monkeypatch):
    enrollment = _enrollment()
    resource = _resource(7)
    node = CertifiedNode(("192.0.2.10", 19800), 9, bytes(32), 17)
    target = OnlineDevice(7000000002, 1, False, 1, bytes(16))
    calls = []

    class FakeSocket:
        def bind(self, address):
            calls.append(("bind", address))

        def close(self):
            calls.append(("close",))

    monkeypatch.setattr(
        alarm_voice_selection.socket, "socket", lambda *_args, **_kwargs: FakeSocket()
    )
    monkeypatch.setattr(
        alarm_voice_selection, "open_camera_session", lambda *_args: (node, target, 40)
    )
    reads = iter(
        (
            ModelReadResult(True, 0, {"setVal": {"supportFunc": 2, "resId": _resource_id(4)}}),
            ModelReadResult(True, 0, {"setVal": {"supportFunc": 2, "resId": _resource_id(4)}}),
            ModelReadResult(True, 0, {"setVal": {"supportFunc": 2, "resId": _resource_id(7, 99)}}),
        )
    )

    def fake_read(_sock, _node, _device, _path, sequence, _timeout, **_kwargs):
        calls.append(("read", sequence))
        return next(reads)

    def fake_write(_sock, _node, _device, selected, sequence, _timeout, **_kwargs):
        calls.append(("write", selected.key, sequence))
        return ModelWriteResult(True, 0)

    monkeypatch.setattr(alarm_voice_selection, "exchange_model_read", fake_read)
    monkeypatch.setattr(alarm_voice_selection, "exchange_alarm_voice_selection_write", fake_write)
    monkeypatch.setattr(alarm_voice_selection.time, "sleep", lambda _seconds: None)

    result = alarm_voice_selection.set_camera_alarm_voice_resource(enrollment, resource)

    assert calls == [
        ("bind", ("", 0)),
        ("read", 40),
        ("write", "system-7", 41),
        ("read", 42),
        ("read", 43),
        ("close",),
    ]
    assert result.previous_logical_number == 4
    assert result.requested_logical_number == 7
    assert result.verified is True


def test_selection_is_idempotent_by_stable_logical_number(monkeypatch):
    enrollment = _enrollment()
    resource = _resource(7)
    node = CertifiedNode(("192.0.2.10", 19800), 9, bytes(32), 17)
    target = OnlineDevice(7000000002, 1, False, 1, bytes(16))

    class FakeSocket:
        def bind(self, _address):
            pass

        def close(self):
            pass

    monkeypatch.setattr(
        alarm_voice_selection.socket, "socket", lambda *_args, **_kwargs: FakeSocket()
    )
    monkeypatch.setattr(
        alarm_voice_selection, "open_camera_session", lambda *_args: (node, target, 40)
    )
    monkeypatch.setattr(
        alarm_voice_selection,
        "exchange_model_read",
        lambda *_args, **_kwargs: ModelReadResult(
            True, 0, {"setVal": {"supportFunc": 1, "resId": _resource_id(7, 999)}}
        ),
    )
    monkeypatch.setattr(
        alarm_voice_selection,
        "exchange_alarm_voice_selection_write",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("write sent")),
    )

    result = alarm_voice_selection.set_camera_alarm_voice_resource(enrollment, resource)

    assert result.changed is False
    assert result.verified is True
