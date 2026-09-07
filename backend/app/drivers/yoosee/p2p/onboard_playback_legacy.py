"""Recovered read-only request format for legacy Yoosee/Gwell SD-card listings."""

from __future__ import annotations

import struct
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta, tzinfo

LEGACY_RECORDING_LIST_COMMAND = 3
LEGACY_RECORDING_LIST_VERSION = 1
LEGACY_RECORDING_LIST_SIZE = 16
LEGACY_FILENAME_PREFIX_SIZE = 6
LEGACY_FILENAME_MAX_SIZE = 512
LEGACY_DURATION_MAX = (1 << 63) - 1
LEGACY_RECORDING_TYPES = frozenset({"A", "M", "S", "V"})


@dataclass(frozen=True, slots=True)
class LegacyPlaybackFile:
    """One vendor filename decoded at the driver boundary.

    ``filename`` stays internal to the Yoosee transport and must not be returned as the generic
    recording ID. The integration layer is responsible for wrapping it in an opaque identifier.
    """

    filename: str
    start_utc: datetime
    end_utc: datetime
    duration_seconds: int
    recording_type: str


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


def _camera_wall_time_to_utc(value: datetime, camera_timezone: tzinfo) -> datetime:
    """Convert an unzoned camera timestamp without silently guessing across DST transitions."""

    candidates: set[datetime] = set()
    for fold in (0, 1):
        local = value.replace(tzinfo=camera_timezone, fold=fold)
        utc = local.astimezone(UTC)
        if utc.astimezone(camera_timezone).replace(tzinfo=None) == value:
            candidates.add(utc)
    if len(candidates) != 1:
        raise ValueError("legacy recording timestamp is ambiguous or nonexistent")
    return candidates.pop()


def parse_legacy_recording_filename(
    filename: str, *, camera_timezone: tzinfo
) -> LegacyPlaybackFile:
    """Parse the filename grammar used by APK ``RecordFileEntity``.

    The legacy response carries camera-local wall time and no timezone. The APK uses the Android
    process timezone implicitly; the server instead requires the camera's explicitly known zone
    and normalizes the result to UTC.
    """

    if camera_timezone is None:
        raise ValueError("camera timezone is required")
    if not isinstance(filename, str):
        raise TypeError("legacy recording filename must be a string")
    if not LEGACY_FILENAME_PREFIX_SIZE < len(filename) <= LEGACY_FILENAME_MAX_SIZE:
        raise ValueError("legacy recording filename length is invalid")
    if "|" in filename or any(ord(char) < 32 for char in filename):
        raise ValueError("legacy recording filename contains invalid characters")

    value = filename[LEGACY_FILENAME_PREFIX_SIZE:]
    first_separator = value.find("_")
    second_separator = value.find("_", first_separator + 1)
    extension_separator = value.find(".")
    duration_start = value.find("(")
    duration_end = value.find(")", duration_start + 1)
    if (
        first_separator != 10
        or second_separator != 19
        or extension_separator <= second_separator + 1
        or duration_start <= extension_separator
        or duration_end <= duration_start + 2
        or duration_end != len(value) - 1
    ):
        raise ValueError("legacy recording filename layout is invalid")

    recording_type = value[extension_separator - 1]
    if recording_type not in LEGACY_RECORDING_TYPES:
        raise ValueError("legacy recording type is unsupported")
    duration_text = value[duration_start + 1 : duration_end - 1]
    if not duration_text.isascii() or not duration_text.isdigit():
        raise ValueError("legacy recording duration is invalid")
    duration_seconds = int(duration_text)
    if not 0 < duration_seconds <= LEGACY_DURATION_MAX:
        raise ValueError("legacy recording duration is out of range")

    try:
        local_start = datetime.strptime(value[:second_separator], "%Y-%m-%d_%H:%M:%S")
        start_utc = _camera_wall_time_to_utc(local_start, camera_timezone)
        end_utc = start_utc + timedelta(seconds=duration_seconds)
    except (OverflowError, ValueError) as exc:
        raise ValueError("legacy recording timestamp is invalid") from exc
    return LegacyPlaybackFile(filename, start_utc, end_utc, duration_seconds, recording_type)
