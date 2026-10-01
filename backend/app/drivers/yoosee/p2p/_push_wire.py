"""Shared bounds and non-cryptographic checksum for offline push frame codecs."""

import struct

MAX_TOKEN_BYTES = 4096  # Local safety ceiling, not a discovered vendor maximum.


def validate_fields(*, push_id: int, access_id: int, session_type: int,
                    session_id: int, token: bytes) -> None:
    for value, bits in ((push_id, 32), (access_id, 64), (session_type, 8), (session_id, 16)):
        if type(value) is not int or not 0 <= value < (1 << bits):
            raise ValueError("push frame field is out of range")
    # Also guarantees safe teardown: its checksum covers the first two token bytes.
    if not isinstance(token, bytes) or not 2 <= len(token) <= MAX_TOKEN_BYTES:
        raise ValueError("push frame token size is unsupported")


def seal_frame(frame: bytearray) -> bytes:
    """SDK helper 0x27a8c0: four rotated body words XOR body length."""
    checksum = struct.unpack_from("<H", frame, 4)[0]
    for index, word in enumerate(struct.unpack_from("<4H", frame, 20)):
        checksum ^= ((word << index) | (word >> (16 - index))) & 0xFFFF
    struct.pack_into("<H", frame, 6, checksum)
    return bytes(frame)
