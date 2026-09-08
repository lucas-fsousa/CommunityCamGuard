"""Socket-free encoding for proven Yoosee onboard-playback speed requests."""

from __future__ import annotations

import struct
from typing import Final

PLAYBACK_SPEED_COMMAND: Final = 24
PLAYBACK_STRATEGY_COMMAND: Final = 25
PLAYBACK_SPEED_LEGACY_SIZE: Final = 4
PLAYBACK_SPEED_EXTENDED_SIZE: Final = 6
PLAYBACK_SUPPORTED_RATES: Final = frozenset({1.0, 2.0, 4.0, 8.0})


def build_playback_speed_request(
    rate: float,
    *,
    device_platform_version: int,
) -> bytes:
    """Build command 24 using the SDK defaults selected by ``setPlayRate``.

    Platform 2 adds video FPS and enabled-media flags. Faster rates use the SDK's defaults of
    four video frames per second with audio dropped; 1x/2x retain both audio and video.
    """

    if isinstance(rate, bool) or not isinstance(rate, (int, float)):
        raise ValueError("playback speed is invalid")
    normalized_rate = float(rate)
    if normalized_rate not in PLAYBACK_SUPPORTED_RATES:
        raise ValueError("playback speed is unsupported")
    if type(device_platform_version) is not int or device_platform_version not in (1, 2):
        raise ValueError("playback speed requires an authoritative platform version")

    rate_milli = int(normalized_rate * 1000)
    if device_platform_version == 1:
        return struct.pack("<I", rate_milli)

    faster_than_2x = normalized_rate > 2.0
    video_fps = 4 if faster_than_2x else 0
    media_flags = 0b10 if faster_than_2x else 0b11
    return struct.pack("<IBB", rate_milli, video_fps, media_flags)


def unpack_playback_speed_request(
    payload: bytes,
    *,
    device_platform_version: int,
) -> tuple[float, int | None, bool | None, bool | None]:
    """Decode command 24 for tests and diagnostics without guessing its platform layout."""

    expected_size = {
        1: PLAYBACK_SPEED_LEGACY_SIZE,
        2: PLAYBACK_SPEED_EXTENDED_SIZE,
    }.get(device_platform_version)
    if type(device_platform_version) is not int or expected_size is None:
        raise ValueError("playback speed requires an authoritative platform version")
    if len(payload) != expected_size:
        raise ValueError("playback speed request size is invalid")

    rate_milli = struct.unpack_from("<I", payload)[0]
    rate = rate_milli / 1000
    if rate not in PLAYBACK_SUPPORTED_RATES:
        raise ValueError("playback speed request rate is unsupported")
    if device_platform_version == 1:
        return rate, None, None, None

    video_fps, media_flags = struct.unpack_from("<BB", payload, 4)
    if media_flags & ~0b11:
        raise ValueError("playback speed media flags are invalid")
    return rate, video_fps, not bool(media_flags & 0b01), not bool(media_flags & 0b10)
