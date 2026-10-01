"""Socket-free SDK 6.45 push hangup encoding; not a production transport.

Identifiers must come from a proven push session, not the existing MTP/B9 route.
See docs/internal/yoosee-push-teardown.md for offsets and unresolved lifecycle.
"""

import struct

from ._push_wire import MAX_TOKEN_BYTES as MAX_TOKEN_BYTES
from ._push_wire import seal_frame, validate_fields


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
    validate_fields(push_id=push_id, access_id=access_id, session_type=session_type,
                    session_id=session_id, token=token)
    frame = bytearray(26 + len(token))
    frame[:2] = b"\x03\x0b"
    struct.pack_into("<H", frame, 4, 6 + len(token))
    struct.pack_into("<IQ", frame, 8, push_id, access_id)
    struct.pack_into("<BBHH", frame, 20, 0, session_type, len(token), session_id)
    frame[26:] = token
    return seal_frame(frame)
