"""Low-level IoTVideo frame primitives shared by bounded P2P codecs."""

from __future__ import annotations

import secrets
import struct

from .crypto import (
    gute_mode1_encrypt,
    gute_mode1_xor_checksum,
    gute_mode2_encrypt,
)


def hash_string(data: bytes) -> int:
    value = 0x4E67C6A7
    for byte in data:
        value ^= ((value << 5) + byte + (value >> 2)) & 0xFFFFFFFF
        value &= 0xFFFFFFFF
    return value


def new_header(subtype: int, length: int, identity: int, sequence: int, flags: int) -> bytearray:
    frame = bytearray(length)
    frame[0] = 0x7F
    frame[1] = subtype
    struct.pack_into("<H", frame, 2, length)
    struct.pack_into("<Q", frame, 4, identity & 0xFFFFFFFFFFFFFFFF)
    struct.pack_into("<I", frame, 0x0C, sequence & 0xFFFFFFFF)
    struct.pack_into("<I", frame, 0x14, flags & 0xFFFFFFFF)
    return frame


def randomized_flags(*, mode: int, proc: int, extra: int = 0) -> int:
    return extra | ((secrets.randbits(15) & 0x7FFF) << 1) | ((mode & 3) << 16) | ((proc & 3) << 18)


def finish_mode1(frame: bytearray) -> bytes:
    struct.pack_into("<I", frame, 0x10, gute_mode1_xor_checksum(frame))
    return gute_mode1_encrypt(bytes(frame))


def finish_mode2(frame: bytearray, session_key: bytes) -> bytes:
    struct.pack_into("<I", frame, 0x10, gute_mode1_xor_checksum(frame))
    return gute_mode2_encrypt(bytes(frame), session_key)
