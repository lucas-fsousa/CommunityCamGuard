"""Recovered read-only request format for legacy Yoosee/Gwell SD-card listings."""

from __future__ import annotations

import struct
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta, tzinfo

LEGACY_RECORDING_LIST_COMMAND = 3
LEGACY_RECORDING_LIST_RESPONSE_COMMAND = 4
LEGACY_RECORDING_LIST_VERSION = 1
LEGACY_RECORDING_LIST_SIZE = 16
LEGACY_FILENAME_MAX_SIZE = 512
LEGACY_DURATION_MAX = (1 << 63) - 1
LEGACY_RECORDING_TYPES = frozenset({"A", "M", "S", "V"})
LEGACY_RESPONSE_HEADER_SIZE = 4
LEGACY_RESPONSE_ITEM_SIZE = 8
LEGACY_RESPONSE_MAX_ITEMS = 128
LEGACY_RESPONSE_HAS_DURATION = 1
JAVA_INT32_MIN = -(1 << 31)
JAVA_INT32_MAX = (1 << 31) - 1


@dataclass(frozen=True, slots=True)
class LegacyPlaybackFile:
    """One vendor filename decoded at the driver boundary.

    ``filename`` stays internal to the Yoosee transport and must not be returned as the generic
    recording ID. The integration layer is responsible for wrapping it in an opaque identifier.
    """

    filename: str
    disc: int
    start_utc: datetime
    end_utc: datetime | None
    duration_seconds: int | None
    recording_type: str


@dataclass(frozen=True, slots=True)
class LegacyPlaybackList:
    command: int
    option0: int
    option1: int
    items: tuple[LegacyPlaybackFile, ...]


def legacy_manager_accepts_decimal(value: str) -> bool:
    """Whether APK ``Integer.parseInt`` can carry this value without truncation or aliases."""

    if not isinstance(value, str) or not value:
        return False
    unsigned = value[1:] if value[0] in "+-" else value
    if not unsigned or not unsigned.isascii() or not unsigned.isdigit():
        return False
    try:
        parsed = int(value, 10)
    except ValueError:
        return False
    return JAVA_INT32_MIN <= parsed <= JAVA_INT32_MAX


def can_use_legacy_playback_manager(device_id: str, password: str) -> bool:
    """Apply both signed-int constraints present immediately before native ``nSendRemoteMsg``."""

    return legacy_manager_accepts_decimal(device_id) and legacy_manager_accepts_decimal(password)


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
    if not 6 < len(filename) <= LEGACY_FILENAME_MAX_SIZE:
        raise ValueError("legacy recording filename length is invalid")
    if "|" in filename or any(ord(char) < 32 for char in filename):
        raise ValueError("legacy recording filename contains invalid characters")

    prefix_end = filename.find("/", 4)
    disc_text = filename[4:prefix_end]
    if (
        not filename.startswith("disc")
        or prefix_end not in (5, 6)
        or not disc_text.isascii()
        or not disc_text.isdigit()
    ):
        raise ValueError("legacy recording filename prefix is invalid")
    disc = int(disc_text)
    if disc > 0x0F:
        raise ValueError("legacy recording disc is out of range")
    value = filename[prefix_end + 1 :]
    first_separator = value.find("_")
    second_separator = value.find("_", first_separator + 1)
    extension_separator = value.find(".")
    duration_start = value.find("(")
    duration_end = value.find(")", duration_start + 1)
    if (
        first_separator != 10
        or second_separator != 19
        or extension_separator <= second_separator + 1
        or value[extension_separator : extension_separator + 3] != ".av"
    ):
        raise ValueError("legacy recording filename layout is invalid")

    recording_type = value[extension_separator - 1]
    if recording_type not in LEGACY_RECORDING_TYPES:
        raise ValueError("legacy recording type is unsupported")
    duration_seconds: int | None = None
    if duration_start == -1 and duration_end == -1:
        if value[extension_separator:] != ".av":
            raise ValueError("legacy recording filename suffix is invalid")
    else:
        if (
            value[extension_separator:duration_start] != ".av "
            or duration_end <= duration_start + 2
            or duration_end != len(value) - 1
            or value[duration_end - 1] != "S"
        ):
            raise ValueError("legacy recording filename duration layout is invalid")
        duration_text = value[duration_start + 1 : duration_end - 1]
        if not duration_text.isascii() or not duration_text.isdigit():
            raise ValueError("legacy recording duration is invalid")
        duration_seconds = int(duration_text)
        if not 0 < duration_seconds <= LEGACY_DURATION_MAX:
            raise ValueError("legacy recording duration is out of range")

    try:
        local_start = datetime.strptime(value[:second_separator], "%Y-%m-%d_%H:%M:%S")
        start_utc = _camera_wall_time_to_utc(local_start, camera_timezone)
        end_utc = (
            start_utc + timedelta(seconds=duration_seconds)
            if duration_seconds is not None
            else None
        )
    except (OverflowError, ValueError) as exc:
        raise ValueError("legacy recording timestamp is invalid") from exc
    return LegacyPlaybackFile(
        filename, disc, start_utc, end_utc, duration_seconds, recording_type
    )


def parse_legacy_recording_list_payload(
    payload: bytes, *, camera_timezone: tzinfo
) -> LegacyPlaybackList:
    """Decode ``sMesgRetRecListType`` as recovered from ``createRecFileJsonData``.

    This parser deliberately stops below the P2P manager/envelope layer. It is safe to use only
    after that layer has authenticated the source camera and isolated the exact response body.
    """

    if camera_timezone is None:
        raise ValueError("camera timezone is required")
    if len(payload) < LEGACY_RESPONSE_HEADER_SIZE:
        raise ValueError("legacy recording-list response is truncated")
    command, option0, option1, item_count = payload[:LEGACY_RESPONSE_HEADER_SIZE]
    if command != LEGACY_RECORDING_LIST_RESPONSE_COMMAND:
        raise ValueError("legacy recording-list response command is invalid")
    if item_count > LEGACY_RESPONSE_MAX_ITEMS:
        raise ValueError("legacy recording-list response has too many items")
    duration_size = 2 * item_count if option0 & LEGACY_RESPONSE_HAS_DURATION else 0
    expected_size = LEGACY_RESPONSE_HEADER_SIZE + LEGACY_RESPONSE_ITEM_SIZE * item_count + duration_size
    if len(payload) != expected_size:
        raise ValueError("legacy recording-list response size is invalid")

    durations_offset = LEGACY_RESPONSE_HEADER_SIZE + LEGACY_RESPONSE_ITEM_SIZE * item_count
    items: list[LegacyPlaybackFile] = []
    for index in range(item_count):
        offset = LEGACY_RESPONSE_HEADER_SIZE + index * LEGACY_RESPONSE_ITEM_SIZE
        year = struct.unpack_from("<H", payload, offset)[0]
        disc_month, day, hour, minute, second, native_type = payload[offset + 2 : offset + 8]
        disc = disc_month >> 4
        month = disc_month & 0x0F
        try:
            recording_type = chr(native_type)
        except ValueError as exc:
            raise ValueError("legacy recording type is invalid") from exc
        duration = (
            struct.unpack_from("<H", payload, durations_offset + index * 2)[0]
            if duration_size
            else None
        )
        duration_suffix = f" ({duration}S)" if duration is not None else ""
        filename = (
            f"disc{disc}/{year:04d}-{month:02d}-{day:02d}_"
            f"{hour:02d}:{minute:02d}:{second:02d}_{recording_type}.av{duration_suffix}"
        )
        items.append(parse_legacy_recording_filename(filename, camera_timezone=camera_timezone))
    return LegacyPlaybackList(command, option0, option1, tuple(items))
