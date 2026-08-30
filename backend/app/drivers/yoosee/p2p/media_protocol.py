"""Pure MTP/KCP and AV-session codecs for Yoosee direct media routes."""

from __future__ import annotations

import struct
import time
from dataclasses import dataclass

MTP_PREFIX_SIZE = 6
MTP_MAX_FRAME_SIZE = 0x7FF
KCP_HEADER = struct.Struct("<IBBHIIII")
KCP_PUSH = 0x51
KCP_ACK = 0x52


@dataclass(frozen=True, slots=True)
class KcpSegment:
    conv: int
    command: int
    fragment: int
    window: int
    timestamp: int
    sequence: int
    unacknowledged: int
    body: bytes


@dataclass(frozen=True, slots=True)
class MediaMeter:
    kind: int
    link_id: int
    flags: int
    source_id: int
    destination_id: int
    sequence: int
    timestamp: int
    channel_type: int
    record_length: int
    role: int
    call_id: int | None


def _rotate_left_16(value: int, shift: int) -> int:
    shift &= 15
    return ((value << shift) | (value >> ((16 - shift) & 15))) & 0xFFFF


def mtp_frame_length(frame: bytes) -> int:
    """Decode the native 11-bit total-length field."""

    if len(frame) < 4:
        raise ValueError("truncated MTP prefix")
    return (frame[2] & 0x07) | (frame[3] << 3)


def mtp_checksum(frame: bytes) -> int:
    """Calculate the rotating-XOR checksum over the first 24 payload bytes."""

    if len(frame) < 30:
        raise ValueError("MTP frame must contain 24 checksum bytes")
    checksum = 0
    for index in range(12):
        word = struct.unpack_from("<H", frame, MTP_PREFIX_SIZE + index * 2)[0]
        checksum ^= _rotate_left_16(word, index)
    return checksum & 0xFFFF


def verify_mtp_frame(frame: bytes) -> bool:
    if len(frame) < 30 or frame[0] != 0xC0 or mtp_frame_length(frame) != len(frame):
        return False
    length_word = struct.unpack_from("<H", frame, 2)[0]
    stored = struct.unpack_from("<H", frame, 4)[0]
    return stored == (mtp_checksum(frame) ^ length_word)


def build_mtp_frame(subtype: int, payload: bytes) -> bytes:
    if not 0 <= subtype <= 0xFF:
        raise ValueError("MTP subtype must fit in one byte")
    if len(payload) < 24:
        raise ValueError("MTP payload must contain at least 24 bytes")
    total = MTP_PREFIX_SIZE + len(payload)
    if total > MTP_MAX_FRAME_SIZE:
        raise ValueError("MTP frame exceeds the 11-bit length field")
    frame = bytearray(total)
    frame[0] = 0xC0
    frame[1] = subtype
    frame[2] = total & 0x07
    frame[3] = total >> 3
    frame[MTP_PREFIX_SIZE:] = payload
    checksum = mtp_checksum(bytes(frame)) ^ struct.unpack_from("<H", frame, 2)[0]
    struct.pack_into("<H", frame, 4, checksum)
    return bytes(frame)


def parse_kcp_segments(frame: bytes) -> tuple[KcpSegment, ...]:
    """Decode every KCP segment coalesced inside one checksummed ``c0/10`` frame."""

    if not verify_mtp_frame(frame) or frame[:2] != b"\xC0\x10":
        raise ValueError("not a valid c0/10 MTP frame")
    segments: list[KcpSegment] = []
    cursor = MTP_PREFIX_SIZE
    while cursor < len(frame):
        if cursor + KCP_HEADER.size > len(frame):
            raise ValueError("truncated KCP header")
        conv, command, fragment, window, timestamp, sequence, una, length = KCP_HEADER.unpack_from(
            frame, cursor
        )
        end = cursor + KCP_HEADER.size + length
        if end > len(frame):
            raise ValueError("truncated KCP payload")
        segments.append(
            KcpSegment(
                conv,
                command,
                fragment,
                window,
                timestamp,
                sequence,
                una,
                bytes(frame[cursor + KCP_HEADER.size : end]),
            )
        )
        cursor = end
    if not segments:
        raise ValueError("empty KCP frame")
    return tuple(segments)


def build_kcp_push(
    conv: int,
    sequence: int,
    body: bytes,
    *,
    timestamp: int | None = None,
    unacknowledged: int = 0,
) -> bytes:
    if timestamp is None:
        timestamp = int(time.monotonic() * 1000) & 0xFFFFFFFF
    segment = KCP_HEADER.pack(
        conv & 0xFFFFFFFF,
        KCP_PUSH,
        0,
        0x100,
        timestamp & 0xFFFFFFFF,
        sequence & 0xFFFFFFFF,
        unacknowledged & 0xFFFFFFFF,
        len(body),
    ) + bytes(body)
    return build_mtp_frame(0x10, segment)


def build_kcp_ack(
    conv: int,
    sequence: int,
    timestamp: int,
    *,
    unacknowledged: int,
) -> bytes:
    segment = KCP_HEADER.pack(
        conv & 0xFFFFFFFF,
        KCP_ACK,
        0,
        0x100,
        timestamp & 0xFFFFFFFF,
        sequence & 0xFFFFFFFF,
        unacknowledged & 0xFFFFFFFF,
        0,
    )
    return build_mtp_frame(0x10, segment)


def build_av_init(call_id: int, *, definition: int = 1) -> bytes:
    """Build the native 76-byte AVSTREAMCTL INIT request."""

    body = bytearray(76)
    body[:4] = bytes((3, 2, 0x4C, 0))
    struct.pack_into("<I", body, 4, call_id & 0xFFFFFFFF)
    struct.pack_into("<I", body, 8, 1)
    struct.pack_into("<I", body, 16, 1)
    struct.pack_into("<I", body, 20, 1)
    struct.pack_into("<I", body, 24, definition & 0xFFFFFFFF)
    body[47] = 0x12
    struct.pack_into("<I", body, 64, 9)
    return bytes(body)


def build_av_control(call_id: int, action: int) -> bytes:
    """Build a native non-INIT AVSTREAMCTL action such as START or CLOSE."""

    body = bytearray(76)
    body[:4] = bytes((3, 0, 0x4C, 0))
    struct.pack_into("<I", body, 4, call_id & 0xFFFFFFFF)
    struct.pack_into("<I", body, 8, action & 0xFFFFFFFF)
    struct.pack_into("<I", body, 64, 9)
    return bytes(body)


def build_media_meter_request(
    access_id: int,
    device_id: int,
    link_id: int,
    call_id: int,
    *,
    sequence: int = 1,
    timestamp: int | None = None,
) -> bytes:
    if timestamp is None:
        timestamp = int(time.monotonic() * 1000) & 0xFFFFFFFF
    inner = bytearray(72)
    inner[1] = 1
    struct.pack_into("<H", inner, 2, 0x44)
    struct.pack_into("<I", inner, 4, link_id & 0xFFFFFFFF)
    struct.pack_into("<I", inner, 8, 8)
    struct.pack_into("<Q", inner, 12, access_id & 0xFFFFFFFFFFFFFFFF)
    struct.pack_into("<Q", inner, 20, device_id & 0xFFFFFFFFFFFFFFFF)
    struct.pack_into("<I", inner, 28, sequence & 0xFFFFFFFF)
    struct.pack_into("<I", inner, 32, timestamp & 0xFFFFFFFF)
    struct.pack_into("<I", inner, 48, 4)
    struct.pack_into("<I", inner, 52, len(inner))
    inner[64:68] = bytes((2, 1, 0, 0))
    struct.pack_into("<I", inner, 68, call_id & 0xFFFFFFFF)
    return build_mtp_frame(0x90, bytes(inner))


def parse_media_meter(frame: bytes) -> MediaMeter | None:
    if not verify_mtp_frame(frame) or frame[:2] != b"\xC0\x90":
        return None
    inner = frame[MTP_PREFIX_SIZE:]
    if len(inner) not in (68, 72) or struct.unpack_from("<H", inner, 2)[0] != 0x44:
        return None
    return MediaMeter(
        kind=inner[1],
        link_id=struct.unpack_from("<I", inner, 4)[0],
        flags=struct.unpack_from("<I", inner, 8)[0],
        source_id=struct.unpack_from("<Q", inner, 12)[0],
        destination_id=struct.unpack_from("<Q", inner, 20)[0],
        sequence=struct.unpack_from("<I", inner, 28)[0],
        timestamp=struct.unpack_from("<I", inner, 32)[0],
        channel_type=struct.unpack_from("<I", inner, 48)[0],
        record_length=struct.unpack_from("<I", inner, 52)[0],
        role=inner[65],
        call_id=struct.unpack_from("<I", inner, 68)[0] if len(inner) == 72 else None,
    )


def build_media_meter_ack(frame: bytes) -> bytes:
    parsed = parse_media_meter(frame)
    if parsed is None or parsed.kind != 1:
        raise ValueError("not a camera meter request")
    inner = bytearray(68)
    inner[1] = 2
    struct.pack_into("<H", inner, 2, 0x44)
    struct.pack_into("<I", inner, 4, parsed.link_id)
    struct.pack_into("<Q", inner, 12, parsed.destination_id)
    struct.pack_into("<Q", inner, 20, parsed.source_id)
    struct.pack_into("<I", inner, 28, parsed.sequence)
    struct.pack_into("<I", inner, 32, parsed.timestamp)
    struct.pack_into("<I", inner, 48, 4)
    struct.pack_into("<I", inner, 52, len(inner))
    inner[65] = 2
    return build_mtp_frame(0x90, bytes(inner))
