from __future__ import annotations

import json
import struct

from backend.app.db.p2p import P2PEnrollment
from backend.app.drivers.contracts import WeeklySchedule
from backend.app.drivers.yoosee.p2p import smart_protection_schedule
from backend.app.drivers.yoosee.p2p.contracts import (
    CertifiedNode,
    ModelReadResult,
    ModelWriteResult,
    OnlineDevice,
)
from backend.app.drivers.yoosee.p2p.crypto import gute_mode2_decrypt


def _enrollment() -> P2PEnrollment:
    return P2PEnrollment("7000000002", 123, bytes(range(64)), None, "now", "now")


def _schedule() -> WeeklySchedule:
    return WeeklySchedule("22:30", "06:15", ("sun", "mon", "fri"))


def test_schedule_builder_maps_sunday_first_mask_and_complete_plan_object():
    node = CertifiedNode(("192.0.2.10", 19800), 9, bytes(range(32)), 17)
    plain = gute_mode2_decrypt(
        smart_protection_schedule.build_smart_protection_schedule_write(
            node, 7000000002, _schedule(), 18, 19
        ),
        node.session_key,
    )

    path_length = plain[0x27]
    value_length = struct.unpack_from("<H", plain, 0x28)[0]
    cursor = 0x32
    assert (
        plain[cursor : cursor + path_length].decode()
        == smart_protection_schedule.SMART_PROTECTION_SCHEDULE_WRITE_PATH
    )
    cursor += path_length + 1
    assert json.loads(plain[cursor : cursor + value_length]) == {
        "start": {"hour": 22, "min": 30},
        "end": {"hour": 6, "min": 15},
        "weekdayEn": 35,
    }


def test_schedule_extractor_round_trips_overnight_and_rejects_invalid_masks():
    native = {"setVal": smart_protection_schedule.native_smart_protection_schedule(_schedule())}

    assert smart_protection_schedule.extract_smart_protection_schedule(native) == _schedule()
    assert (
        smart_protection_schedule.extract_smart_protection_schedule(
            {"start": {"hour": 0, "min": 0}, "end": {"hour": 0, "min": 0}, "weekdayEn": 0}
        )
        is None
    )
    assert (
        smart_protection_schedule.extract_smart_protection_schedule(
            {"start": {"hour": 0, "min": 0}, "end": {"hour": 0, "min": 0}, "weekdayEn": 128}
        )
        is None
    )


def test_schedule_change_requires_complete_preflight_and_exact_readback(monkeypatch):
    enrollment = _enrollment()
    requested = _schedule()
    previous = WeeklySchedule("00:00", "00:00", ("sun", "mon", "tue", "wed", "thu", "fri", "sat"))
    node = CertifiedNode(("192.0.2.10", 19800), 9, bytes(32), 17)
    target = OnlineDevice(7000000002, 1, False, 1, bytes(16))
    calls = []

    class FakeSocket:
        def bind(self, address):
            calls.append(("bind", address))

        def close(self):
            calls.append(("close",))

    monkeypatch.setattr(
        smart_protection_schedule.socket,
        "socket",
        lambda *_args, **_kwargs: FakeSocket(),
    )
    monkeypatch.setattr(
        smart_protection_schedule,
        "open_camera_session",
        lambda *_args: (node, target, 40),
    )
    reads = iter(
        (
            ModelReadResult(
                True,
                0,
                {"setVal": smart_protection_schedule.native_smart_protection_schedule(previous)},
            ),
            ModelReadResult(
                True,
                0,
                {"setVal": smart_protection_schedule.native_smart_protection_schedule(previous)},
            ),
            ModelReadResult(
                True,
                0,
                {"setVal": smart_protection_schedule.native_smart_protection_schedule(requested)},
            ),
        )
    )

    def fake_read(_sock, _node, _device, _path, sequence, _timeout, **_kwargs):
        calls.append(("read", sequence))
        return next(reads)

    def fake_write(_sock, _node, _device, schedule, sequence, _timeout, **_kwargs):
        calls.append(("write", schedule, sequence))
        return ModelWriteResult(True, 0)

    monkeypatch.setattr(smart_protection_schedule, "exchange_model_read", fake_read)
    monkeypatch.setattr(
        smart_protection_schedule,
        "exchange_smart_protection_schedule_write",
        fake_write,
    )
    monkeypatch.setattr(smart_protection_schedule.time, "sleep", lambda _seconds: None)

    result = smart_protection_schedule.set_camera_smart_protection_schedule(enrollment, requested)

    assert calls == [
        ("bind", ("", 0)),
        ("read", 40),
        ("write", requested, 41),
        ("read", 42),
        ("read", 43),
        ("close",),
    ]
    assert result.previous_schedule == previous
    assert result.schedule == requested
    assert result.verified is True


def test_schedule_change_is_idempotent(monkeypatch):
    enrollment = _enrollment()
    schedule = _schedule()
    node = CertifiedNode(("192.0.2.10", 19800), 9, bytes(32), 17)
    target = OnlineDevice(7000000002, 1, False, 1, bytes(16))

    class FakeSocket:
        def bind(self, _address):
            pass

        def close(self):
            pass

    monkeypatch.setattr(
        smart_protection_schedule.socket,
        "socket",
        lambda *_args, **_kwargs: FakeSocket(),
    )
    monkeypatch.setattr(
        smart_protection_schedule,
        "open_camera_session",
        lambda *_args: (node, target, 40),
    )
    monkeypatch.setattr(
        smart_protection_schedule,
        "exchange_model_read",
        lambda *_args, **_kwargs: ModelReadResult(
            True,
            0,
            {"setVal": smart_protection_schedule.native_smart_protection_schedule(schedule)},
        ),
    )
    monkeypatch.setattr(
        smart_protection_schedule,
        "exchange_smart_protection_schedule_write",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("write sent")),
    )

    result = smart_protection_schedule.set_camera_smart_protection_schedule(enrollment, schedule)

    assert result.changed is False
    assert result.verified is True
