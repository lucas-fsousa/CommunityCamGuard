"""Offline RTC AV subframe boundaries and raw SDK output fields."""

import struct
from dataclasses import dataclass, field


@dataclass(frozen=True)
class RTCAVUnit:
    discriminator: int
    clock_raw: int  # Units are not yet established.
    flag_raw: int
    index_raw: int  # SDK byte +10 minus one; may be -1.
    tag_raw: int
    payload: bytes = field(repr=False)


def parse_rtc_av_units(frame: bytes) -> tuple[RTCAVUnit, ...]:
    """Parse one plaintext type-0x80 record, without codec/media inference.

    Local bound permits reassembled records up to 256 KiB. Grouped parsing is
    confined to this record, not the SDK's remaining ring. Empty groups and
    unconsumed tails fail closed. Authentication/decryption remain caller-owned.
    """
    if not isinstance(frame, bytes) or not 24 <= len(frame) <= 256 * 1024:
        raise ValueError("RTC AV size is invalid")
    kind, _, body_size = struct.unpack_from("<HHI", frame)
    if kind != 0x80 or body_size != len(frame) - 8:
        raise ValueError("RTC AV type or length is invalid")
    flags, index, tag = frame[8], frame[10] - 1, frame[12]
    clock = struct.unpack_from("<Q", frame, 16)[0]
    if not flags & 1:
        if not flags & 2:
            raise ValueError("RTC AV payload branch is unsupported")
        return (RTCAVUnit(1, clock, (flags >> 2) & 1, index, tag, frame[24:]),)
    count = frame[11] & 15
    if not count:
        raise ValueError("RTC AV group is empty")
    units = []
    offset = 24
    for _ in range(count):
        if len(frame) - offset < 8:
            raise ValueError("RTC AV subframe prefix is truncated")
        length, delta = struct.unpack_from("<HI", frame, offset)
        start = offset + 8
        end = start + length
        if end > len(frame):
            raise ValueError("RTC AV subframe payload is truncated")
        units.append(RTCAVUnit(0, (clock + delta) & 0xFFFFFFFFFFFFFFFF,
                               1, index, tag, frame[start:end]))
        offset = end
    if offset != len(frame):
        raise ValueError("RTC AV group has unconsumed bytes")
    return tuple(units)
