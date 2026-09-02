"""Recovered read-only request format for legacy Yoosee/Gwell SD-card listings."""

from __future__ import annotations

import struct
from datetime import datetime, timedelta, tzinfo

LEGACY_RECORDING_LIST_COMMAND = 3
LEGACY_RECORDING_LIST_VERSION = 1
LEGACY_RECORDING_LIST_SIZE = 16


def _require_utc(value: datetime, label: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None or value.utcoffset() != timedelta(0):
        raise ValueError(f"{label} must be UTC")


def _minute_bounds(
    start_utc: datetime, end_utc: datetime, camera_timezone: tzinfo
) -> tuple[datetime, datetime]:
    _require_utc(start_utc, "start")
    _require_utc(end_utc, "end")
    if end_utc <= start_utc:
        raise ValueError("recording-list window must be positive")
    start = start_utc.astimezone(camera_timezone).replace(second=0, microsecond=0)
    end = end_utc.astimezone(camera_timezone)
    if end.second or end.microsecond:
        end += timedelta(minutes=1)
    return start, end.replace(second=0, microsecond=0)


def build_legacy_recording_list_request(
    start_utc: datetime,
    end_utc: datetime,
    *,
    camera_timezone: tzinfo,
) -> bytes:
    """Build the 16-byte ``nGetRobotRecordList`` body recovered from the APK JNI.

    The wire format carries camera-local wall-clock minutes and no timezone or seconds. UTC bounds
    are therefore expanded outwards to whole minutes only after conversion to the exact camera
    timezone. The response must perform the inverse conversion before entering the generic archive
    contract.
    """

    if camera_timezone is None:
        raise ValueError("camera timezone is required")
    start, end = _minute_bounds(start_utc, end_utc, camera_timezone)
    if not 1970 <= start.year <= 0xFFFF or not 1970 <= end.year <= 0xFFFF:
        raise ValueError("recording-list year is out of range")
    return struct.pack(
        "<BBHHBBBBHBBBB",
        LEGACY_RECORDING_LIST_COMMAND,
        LEGACY_RECORDING_LIST_VERSION,
        0,
        start.year,
        start.month,
        start.day,
        start.hour,
        start.minute,
        end.year,
        end.month,
        end.day,
        end.hour,
        end.minute,
    )


def unpack_legacy_recording_list_request(payload: bytes) -> tuple[datetime, datetime]:
    """Decode test/diagnostic payloads as naive camera-local wall-clock values."""

    if len(payload) != LEGACY_RECORDING_LIST_SIZE:
        raise ValueError("legacy recording-list request must contain 16 bytes")
    command, version, reserved, sy, sm, sd, sh, smin, ey, em, ed, eh, emin = struct.unpack(
        "<BBHHBBBBHBBBB", payload
    )
    if (command, version, reserved) != (
        LEGACY_RECORDING_LIST_COMMAND,
        LEGACY_RECORDING_LIST_VERSION,
        0,
    ):
        raise ValueError("legacy recording-list request header is invalid")
    try:
        return datetime(sy, sm, sd, sh, smin), datetime(ey, em, ed, eh, emin)
    except ValueError as exc:
        raise ValueError("legacy recording-list request date is invalid") from exc
