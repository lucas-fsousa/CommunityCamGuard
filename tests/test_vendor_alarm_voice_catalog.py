from __future__ import annotations

import base64
import json
import struct
from typing import ClassVar

import pytest

from backend.app.db.p2p import P2PEnrollment
from backend.app.drivers.yoosee.p2p import alarm_voice_catalog
from backend.app.drivers.yoosee.p2p.contracts import (
    CertifiedNode,
    OnlineDevice,
    P2PProbeError,
)
from backend.app.drivers.yoosee.p2p.resource_service_session import AlarmVoiceCatalogResult

ENROLLMENT = P2PEnrollment("7000000002", 123, bytes(64), None, "now", "now")
NODE = CertifiedNode(("192.0.2.10", 19800), 9, bytes(32), 17)
DEVICE = OnlineDevice(7000000002, 1, False, 1, bytes(16))


def _resource_id(number: int) -> str:
    return base64.b64encode(struct.pack("<II16s", 4, number, bytes(16))).decode("ascii")


def _payload(*, system: bool, number: int | None) -> bytes:
    resources = []
    if number is not None:
        resources.append(
            {
                "resType": 4,
                "resId": _resource_id(number),
                "isSys": int(system),
                "desc": json.dumps(
                    {"name": f"Voice {number}", "audioFormat": "amr", "duration": 1200}
                ),
                "downloadUrl": "https://secret.invalid/signed-token",
            }
        )
    return json.dumps(
        {"code": 0, "data": {"total": len(resources), "urls": resources}}
    ).encode()


class FakeSocket:
    instances: ClassVar[list[FakeSocket]] = []

    def __init__(self, *_args) -> None:
        self.bound = None
        self.closed = False
        self.__class__.instances.append(self)

    def bind(self, address) -> None:
        self.bound = address

    def close(self) -> None:
        self.closed = True


def test_catalog_orchestrator_reads_both_sources_and_exposes_only_sanitized_options(monkeypatch):
    FakeSocket.instances.clear()
    calls: list[tuple[dict[str, object], int]] = []
    responses = iter(
        (
            AlarmVoiceCatalogResult(True, 0, _payload(system=True, number=1), False, 2),
            AlarmVoiceCatalogResult(True, 0, _payload(system=False, number=7), False, 1),
        )
    )
    monkeypatch.setattr(alarm_voice_catalog.socket, "socket", FakeSocket)
    monkeypatch.setattr(
        alarm_voice_catalog,
        "open_camera_session",
        lambda *_args: (NODE, DEVICE, 0xFFFFFFFF),
    )

    def exchange(_sock, _node, query, sequence, _timeout, **_kwargs):
        calls.append((query, sequence))
        return next(responses)

    monkeypatch.setattr(alarm_voice_catalog, "exchange_alarm_voice_catalog", exchange)

    result = alarm_voice_catalog.read_camera_alarm_voice_catalog(ENROLLMENT, language="pt-BR")

    assert result.device_id == ENROLLMENT.device_id
    assert result.system_total == 1
    assert result.custom_total == 1
    assert result.transport_acknowledged is True
    assert result.public_options() == (
        {"key": "system-1", "label": "Voice 1", "duration_ms": 1200, "system": True},
        {"key": "custom-7", "label": "Voice 7", "duration_ms": 1200, "system": False},
    )
    assert "secret" not in repr(result)
    assert calls[0][0]["keyWord"] == "language_8"
    assert calls[0][0]["bySys"] == 1
    assert calls[1][0]["bySys"] == 0
    assert "keyWord" not in calls[1][0]
    assert [sequence for _query, sequence in calls] == [0xFFFFFFFF, 0]
    assert FakeSocket.instances[0].bound == ("", 0)
    assert FakeSocket.instances[0].closed is True


@pytest.mark.parametrize(
    "response",
    (
        AlarmVoiceCatalogResult(False, None, None, False, 0),
        AlarmVoiceCatalogResult(True, 500, b"{}", False, 0),
        AlarmVoiceCatalogResult(True, 0, b"not-json", False, 0),
        AlarmVoiceCatalogResult(True, 0, b'{"code":9,"data":{"total":0,"urls":[]}}', False, 0),
    ),
)
def test_catalog_orchestrator_fails_closed_on_invalid_service_or_metadata(monkeypatch, response):
    FakeSocket.instances.clear()
    monkeypatch.setattr(alarm_voice_catalog.socket, "socket", FakeSocket)
    monkeypatch.setattr(
        alarm_voice_catalog,
        "open_camera_session",
        lambda *_args: (NODE, DEVICE, 10),
    )
    monkeypatch.setattr(
        alarm_voice_catalog,
        "exchange_alarm_voice_catalog",
        lambda *_args, **_kwargs: response,
    )

    with pytest.raises(P2PProbeError, match="catalogue"):
        alarm_voice_catalog.read_camera_alarm_voice_catalog(ENROLLMENT)

    assert FakeSocket.instances[0].closed is True


def test_catalog_orchestrator_rejects_cross_source_key_conflicts(monkeypatch):
    FakeSocket.instances.clear()
    duplicate = _payload(system=True, number=1)
    responses = iter(
        (
            AlarmVoiceCatalogResult(True, 0, duplicate, False, 1),
            AlarmVoiceCatalogResult(True, 0, duplicate, False, 1),
        )
    )
    monkeypatch.setattr(alarm_voice_catalog.socket, "socket", FakeSocket)
    monkeypatch.setattr(
        alarm_voice_catalog,
        "open_camera_session",
        lambda *_args: (NODE, DEVICE, 10),
    )
    monkeypatch.setattr(
        alarm_voice_catalog,
        "exchange_alarm_voice_catalog",
        lambda *_args, **_kwargs: next(responses),
    )

    with pytest.raises(P2PProbeError, match="conflicting"):
        alarm_voice_catalog.read_camera_alarm_voice_catalog(ENROLLMENT)
