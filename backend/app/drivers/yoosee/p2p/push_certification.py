"""Socket-free SDK 6.45 one-terminal certification encoding; not a transport."""

import struct

from ._push_wire import seal_frame, validate_fields


def build_push_certification(
    *, push_id: int, access_id: int, session_type: int, session_id: int,
    token: bytes, device_id: int,
) -> bytes:
    """Reproduce iv_send_push_certify_frame's single-terminal branch only.

    device_id comes from E4 +0x38; session_id is local and may change on a
    recertification request. Producing bytes is not certification or authentication.
    The common two-byte token floor preserves compatibility with safe hangup.
    """
    validate_fields(push_id=push_id, access_id=access_id, session_type=session_type,
                    session_id=session_id, token=token)
    if type(device_id) is not int or not 0 < device_id <= 0xFFFFFFFFFFFFFFFF:
        raise ValueError("push certification device id is invalid")
    frame = bytearray(36 + len(token))
    frame[:2] = b"\x03\x06"
    struct.pack_into("<H", frame, 4, 16 + len(token))
    struct.pack_into("<IQ", frame, 8, push_id, access_id)
    struct.pack_into("<BBHHH", frame, 20, session_type, 1, len(token) + 8,
                     len(token), session_id)
    frame[28:28 + len(token)] = token
    struct.pack_into("<Q", frame, 28 + len(token), device_id)
    return seal_frame(frame)
