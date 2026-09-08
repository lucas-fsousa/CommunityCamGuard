"""Passive decoding of authoritative IoTVideo device-platform metadata."""

from __future__ import annotations

import struct
from dataclasses import dataclass
from typing import Final

PUSH_STREAM_DISTRIBUTE_TYPE: Final = 0xE4
PUSH_STREAM_DISTRIBUTE_BASE_SIZE: Final = 0x8A
PUSH_STREAM_DISTRIBUTE_MAX_SIZE: Final = 4096
_V4_RELAY_SIZE: Final = 16
_V6_RELAY_SIZE: Final = 28


@dataclass(frozen=True, slots=True)
class DevicePlatformMetadata:
    device_id: int
    version: int
    new_platform: bool


def parse_push_stream_platform_metadata(
    frame: bytes,
    *,
    expected_device_id: int,
) -> DevicePlatformMetadata | None:
    """Parse one decrypted GAT E4 distribution frame without opening a connection.

    The native SDK defaults a device to platform 1 and promotes it to platform 2 only when this
    frame's option bit 0 is set. Relay-table bounds are checked so unrelated/truncated E4 data
    cannot become authoritative metadata.
    """

    if type(expected_device_id) is not int or not 0 < expected_device_id <= 0xFFFFFFFFFFFFFFFF:
        raise ValueError("expected platform-metadata device id is invalid")
    if not PUSH_STREAM_DISTRIBUTE_BASE_SIZE <= len(frame) <= PUSH_STREAM_DISTRIBUTE_MAX_SIZE:
        return None
    if frame[0] not in (0x7E, 0x7F) or frame[1] != PUSH_STREAM_DISTRIBUTE_TYPE:
        return None
    if struct.unpack_from("<H", frame, 2)[0] != len(frame):
        return None
    flags = struct.unpack_from("<I", frame, 0x14)[0]
    if flags & (1 << 20):
        return None

    token_size = struct.unpack_from("<H", frame, 0x1E)[0]
    relay_offset = 0x88 + token_size
    if relay_offset + 2 > len(frame):
        return None
    v4_relay_count = frame[relay_offset]
    v6_relay_count = frame[relay_offset + 1]
    relay_end = (
        relay_offset
        + 2
        + v4_relay_count * _V4_RELAY_SIZE
        + v6_relay_count * _V6_RELAY_SIZE
    )
    if relay_end > len(frame):
        return None

    device_id = struct.unpack_from("<Q", frame, 0x38)[0]
    if device_id != expected_device_id:
        return None
    new_platform = bool(frame[0x18] & 1)
    return DevicePlatformMetadata(
        device_id=device_id,
        version=2 if new_platform else 1,
        new_platform=new_platform,
    )
