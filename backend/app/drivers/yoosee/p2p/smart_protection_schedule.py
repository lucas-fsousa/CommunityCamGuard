"""Typed weekly schedule for the Yoosee smart-protection guard."""

from __future__ import annotations

import json
import secrets
import socket
import struct
import time
from dataclasses import dataclass

from ....db.p2p import P2PEnrollment
from ...contracts import Weekday, WeeklySchedule
from .camera_session import open_camera_session
from .contracts import CertifiedNode, ModelWriteResult, OnlineDevice, P2PProbeError
from .model_session import exchange_model_read
from .model_write_session import exchange_model_write_request
from .wire import finish_mode2, new_header, randomized_flags

SMART_PROTECTION_SCHEDULE_READ_PATH = "ProWritable.guardParm"
SMART_PROTECTION_SCHEDULE_WRITE_PATH = "ProWritable.guardParm.setVal.plan"
WEEKDAY_BITS: dict[Weekday, int] = {
    "sun": 1 << 0,
    "mon": 1 << 1,
    "tue": 1 << 2,
    "wed": 1 << 3,
    "thu": 1 << 4,
    "fri": 1 << 5,
    "sat": 1 << 6,
}


@dataclass(frozen=True, slots=True)
class P2PSmartProtectionScheduleState:
    device_id: str
    schedule: WeeklySchedule
    authenticated: bool
    direct_handshake: bool
    transport_acknowledged: bool
    error_code: int | None


@dataclass(frozen=True, slots=True)
class P2PSmartProtectionScheduleWrite:
    device_id: str
    schedule: WeeklySchedule
    previous_schedule: WeeklySchedule
    changed: bool
    transport_acknowledged: bool
    error_code: int | None
    verified: bool


def _clock(value: str) -> dict[str, int]:
    hour, minute = value.split(":")
    return {"hour": int(hour), "min": int(minute)}


def native_smart_protection_schedule(schedule: WeeklySchedule) -> dict[str, object]:
    """Map the generic schedule to the APK's Sunday-first guard-plan object."""

    if not isinstance(schedule, WeeklySchedule):
        raise ValueError("smart-protection schedule must be a WeeklySchedule")
    mask = 0
    for day in schedule.weekdays:
        mask |= WEEKDAY_BITS[day]
    return {"start": _clock(schedule.start), "end": _clock(schedule.end), "weekdayEn": mask}


def _parse_clock(value: object) -> str | None:
    if not isinstance(value, dict):
        return None
    hour = value.get("hour")
    minute = value.get("min")
    if type(hour) is not int or type(minute) is not int:
        return None
    if not 0 <= hour <= 23 or not 0 <= minute <= 59:
        return None
    return f"{hour:02d}:{minute:02d}"


def extract_smart_protection_schedule(value: object) -> WeeklySchedule | None:
    """Extract only a complete, supported guard plan from a nested model response."""

    if not isinstance(value, dict):
        return None
    if {"start", "end", "weekdayEn"}.issubset(value):
        start = _parse_clock(value.get("start"))
        end = _parse_clock(value.get("end"))
        mask = value.get("weekdayEn")
        if start is None or end is None or type(mask) is not int or not 1 <= mask <= 0x7F:
            return None
        weekdays = tuple(day for day, bit in WEEKDAY_BITS.items() if mask & bit)
        return WeeklySchedule(start, end, weekdays)
    for key in ("plan", "setVal", "guardParm", "ProWritable"):
        if key in value:
            candidate = extract_smart_protection_schedule(value[key])
            if candidate is not None:
                return candidate
    return None


def build_smart_protection_schedule_write(
    node: CertifiedNode,
    device_id: int,
    schedule: WeeklySchedule,
    sequence: int,
    message_id: int,
) -> bytes:
    """Build the one allowed aggregate D2 plan write from a validated domain value."""

    encoded_path = SMART_PROTECTION_SCHEDULE_WRITE_PATH.encode("utf-8")
    encoded_json = json.dumps(
        native_smart_protection_schedule(schedule), separators=(",", ":")
    ).encode("ascii")
    length = 0x2A + 8 + len(encoded_path) + 1 + len(encoded_json) + 1
    frame = new_header(
        0xD2,
        length,
        node.session_id,
        sequence,
        randomized_flags(mode=2, proc=3),
    )
    frame[0] = 0x7E
    frame[0x18] = 2
    struct.pack_into("<I", frame, 0x20, message_id & 0x7FFFFFFF)
    struct.pack_into("<H", frame, 0x24, 1)
    frame[0x26] = 7
    frame[0x27] = len(encoded_path)
    struct.pack_into("<H", frame, 0x28, len(encoded_json))
    cursor = 0x2A
    struct.pack_into("<Q", frame, cursor, device_id)
    cursor += 8
    frame[cursor : cursor + len(encoded_path)] = encoded_path
    cursor += len(encoded_path) + 1
    frame[cursor : cursor + len(encoded_json)] = encoded_json
    return finish_mode2(frame, node.session_key)


def exchange_smart_protection_schedule_write(
    sock: socket.socket,
    node: CertifiedNode,
    device: OnlineDevice,
    schedule: WeeklySchedule,
    sequence: int,
    timeout: float,
    *,
    retries: int = 3,
    deadline: float | None = None,
) -> ModelWriteResult:
    if not isinstance(schedule, WeeklySchedule):
        raise ValueError("smart-protection schedule must be a WeeklySchedule")
    message_id = secrets.randbits(31)
    request = build_smart_protection_schedule_write(
        node, device.device_id, schedule, sequence, message_id
    )
    return exchange_model_write_request(
        sock,
        node,
        request,
        message_id,
        timeout,
        retries=retries,
        deadline=deadline,
    )


def read_camera_smart_protection_schedule(
    enrollment: P2PEnrollment,
    *,
    timeout: float = 1.5,
    total_timeout: float = 25.0,
) -> P2PSmartProtectionScheduleState:
    bounded_timeout = max(0.5, min(float(timeout), 5.0))
    deadline = time.monotonic() + max(8.0, min(float(total_timeout), 35.0))
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("", 0))
    try:
        node, target, sequence = open_camera_session(sock, enrollment, bounded_timeout, deadline)
        result = exchange_model_read(
            sock,
            node,
            target,
            SMART_PROTECTION_SCHEDULE_READ_PATH,
            sequence,
            min(5.0, max(0.5, deadline - time.monotonic())),
            deadline=deadline,
        )
        schedule = extract_smart_protection_schedule(result.value)
        if result.error_code != 0 or schedule is None:
            raise P2PProbeError("camera returned no supported smart-protection schedule")
    except P2PProbeError:
        raise
    except (OSError, ValueError) as exc:
        raise P2PProbeError("P2P smart-protection schedule read failed") from exc
    finally:
        sock.close()
    return P2PSmartProtectionScheduleState(
        enrollment.device_id,
        schedule,
        True,
        False,
        result.transport_acknowledged,
        result.error_code,
    )


def set_camera_smart_protection_schedule(
    enrollment: P2PEnrollment,
    schedule: WeeklySchedule,
    *,
    timeout: float = 1.5,
    total_timeout: float = 30.0,
) -> P2PSmartProtectionScheduleWrite:
    """Replace only the complete guard plan, with preflight and exact readback."""

    if not isinstance(schedule, WeeklySchedule):
        raise ValueError("smart-protection schedule must be a WeeklySchedule")
    bounded_timeout = max(0.5, min(float(timeout), 5.0))
    deadline = time.monotonic() + max(10.0, min(float(total_timeout), 40.0))
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("", 0))
    try:
        node, target, sequence = open_camera_session(sock, enrollment, bounded_timeout, deadline)
        preflight = exchange_model_read(
            sock,
            node,
            target,
            SMART_PROTECTION_SCHEDULE_READ_PATH,
            sequence,
            min(5.0, max(0.5, deadline - time.monotonic())),
            deadline=deadline,
        )
        previous = extract_smart_protection_schedule(preflight.value)
        if preflight.error_code != 0 or previous is None:
            raise P2PProbeError("smart-protection schedule preflight returned no supported state")
        if previous == schedule:
            return P2PSmartProtectionScheduleWrite(
                enrollment.device_id, schedule, previous, False, False, 0, True
            )
        write = exchange_smart_protection_schedule_write(
            sock,
            node,
            target,
            schedule,
            (sequence + 1) & 0xFFFFFFFF,
            bounded_timeout,
            deadline=deadline,
        )
        if write.error_code != 0:
            raise P2PProbeError("camera rejected the smart-protection schedule")
        verified = False
        for attempt in range(5):
            if attempt:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                time.sleep(min(0.5, remaining))
            readback = exchange_model_read(
                sock,
                node,
                target,
                SMART_PROTECTION_SCHEDULE_READ_PATH,
                (sequence + 2 + attempt) & 0xFFFFFFFF,
                min(bounded_timeout, max(0.5, deadline - time.monotonic())),
                retries=1,
                deadline=deadline,
            )
            if (
                readback.error_code == 0
                and extract_smart_protection_schedule(readback.value) == schedule
            ):
                verified = True
                break
        if not verified:
            raise P2PProbeError("camera did not confirm the smart-protection schedule")
    except P2PProbeError:
        raise
    except (OSError, ValueError) as exc:
        raise P2PProbeError("P2P smart-protection schedule change failed") from exc
    finally:
        sock.close()
    return P2PSmartProtectionScheduleWrite(
        enrollment.device_id,
        schedule,
        previous,
        True,
        write.transport_acknowledged,
        write.error_code,
        True,
    )
