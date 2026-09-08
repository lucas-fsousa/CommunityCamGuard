"""Socket-free codec for the initial Yoosee onboard-playback link metadata."""

from __future__ import annotations

import struct
from dataclasses import dataclass
from typing import Final

PLAYBACK_LINK_USER_DATA_SIZE: Final = 32
PLAYBACK_LINK_INITIAL_RATE_MILLI: Final = 1000
PLAYBACK_LINK_PLATFORM2_AUXILIARY: Final = 0x0300
_MAX_U64: Final = 0xFFFFFFFFFFFFFFFF


@dataclass(frozen=True, slots=True)
class InitialPlaybackLinkMetadata:
    playback_time_ms: int
    file_start_time_ms: int
    source_id: int
    rate_milli: int
    platform_auxiliary: int
    camera_id: int


def _wire_milliseconds(value: int, field: str) -> int:
    if type(value) is not int or not 0 <= value <= _MAX_U64 * 1000 + 999:
        raise ValueError(f"playback link {field} is invalid")
    return value // 1000


def _platform_version(value: int) -> int:
    if type(value) is not int or value not in (1, 2):
        raise ValueError("playback link requires an authoritative platform version")
    return value


def build_initial_playback_link_user_data(
    file_start_time_us: int,
    playback_time_us: int | None = None,
    *,
    source_id: int = 0,
    camera_id: int = -1,
    device_platform_version: int,
) -> bytes:
    """Build the native 32-byte request-userdata block used to open SD playback.

    Public SDK timestamps are microseconds while the link stores milliseconds. The current app's
    ordinary SD route uses source zero and camera ``-1`` (encoded as byte ``255``). Version 2's
    auxiliary value is intentionally kept opaque until its semantic name is proven.
    """

    platform_version = _platform_version(device_platform_version)
    start_ms = _wire_milliseconds(file_start_time_us, "file start time")
    selected_us = file_start_time_us if playback_time_us is None else playback_time_us
    playback_ms = _wire_milliseconds(selected_us, "selected time")
    if type(source_id) is not int or not 0 <= source_id <= 0xFFFF:
        raise ValueError("playback link source id is invalid")
    if type(camera_id) is not int or not -1 <= camera_id <= 0xFE:
        raise ValueError("playback link camera id is invalid")

    payload = bytearray(PLAYBACK_LINK_USER_DATA_SIZE)
    struct.pack_into("<Q", payload, 1, playback_ms)
    struct.pack_into("<Q", payload, 9, start_ms)
    struct.pack_into("<H", payload, 17, source_id)
    struct.pack_into("<I", payload, 19, PLAYBACK_LINK_INITIAL_RATE_MILLI)
    if platform_version == 2:
        struct.pack_into("<H", payload, 23, PLAYBACK_LINK_PLATFORM2_AUXILIARY)
    payload[25] = camera_id & 0xFF
    return bytes(payload)


def parse_initial_playback_link_user_data(
    payload: bytes,
    *,
    device_platform_version: int,
) -> InitialPlaybackLinkMetadata:
    """Strictly decode an initial playback-link block for tests and diagnostics."""

    platform_version = _platform_version(device_platform_version)
    if len(payload) != PLAYBACK_LINK_USER_DATA_SIZE:
        raise ValueError("playback link user data size is invalid")
    if payload[0] != 0 or any(payload[26:]):
        raise ValueError("playback link reserved bytes are invalid")

    rate_milli = struct.unpack_from("<I", payload, 19)[0]
    auxiliary = struct.unpack_from("<H", payload, 23)[0]
    expected_auxiliary = PLAYBACK_LINK_PLATFORM2_AUXILIARY if platform_version == 2 else 0
    if rate_milli != PLAYBACK_LINK_INITIAL_RATE_MILLI or auxiliary != expected_auxiliary:
        raise ValueError("playback link initial rate metadata is invalid")

    return InitialPlaybackLinkMetadata(
        playback_time_ms=struct.unpack_from("<Q", payload, 1)[0],
        file_start_time_ms=struct.unpack_from("<Q", payload, 9)[0],
        source_id=struct.unpack_from("<H", payload, 17)[0],
        rate_milli=rate_milli,
        platform_auxiliary=auxiliary,
        camera_id=payload[25],
    )
