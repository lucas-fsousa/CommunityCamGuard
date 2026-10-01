"""Socket-free SDK 6.45 push hangup encoding; not a production transport.

Identifiers must come from a proven push session, not the existing MTP/B9 route.
See docs/internal/yoosee-push-teardown.md for offsets and unresolved lifecycle.
"""

import struct

MAX_TOKEN_BYTES = 4096  # Local safety ceiling, not a discovered vendor maximum.


def _uint(value: int, bits: int) -> None:
    if type(value) is not int or not 0 <= value < (1 << bits):
        raise ValueError("push hangup field is out of range")


def build_push_hangup(
    *,
    push_id: int,
    access_id: int,
    session_type: int,
    session_id: int,
    token: bytes,
) -> bytes:
    """Encode only iv_push_start_hanghup's reason-zero variant.

    SDK checksum reads eight body bytes, including the first two token bytes.
    Short tokens are rejected instead of reproducing an out-of-bounds native read.
    Field names denote push-session values; no MTP identifier equivalence is implied.
    """
    for value, bits in ((push_id, 32), (access_id, 64), (session_type, 8), (session_id, 16)):
        _uint(value, bits)
    if not isinstance(token, bytes) or not 2 <= len(token) <= MAX_TOKEN_BYTES:
        raise ValueError("push hangup token size is unsupported")
    frame = bytearray(26 + len(token))
    frame[:2] = b"\x03\x0b"
    struct.pack_into("<H", frame, 4, 6 + len(token))
    struct.pack_into("<IQ", frame, 8, push_id, access_id)
    struct.pack_into("<BBHH", frame, 20, 0, session_type, len(token), session_id)
    frame[26:] = token
    checksum = 6 + len(token)
    for index, word in enumerate(struct.unpack_from("<4H", frame, 20)):
        checksum ^= ((word << index) | (word >> (16 - index))) & 0xFFFF
    struct.pack_into("<H", frame, 6, checksum)
    return bytes(frame)
