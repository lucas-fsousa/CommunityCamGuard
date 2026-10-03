"""Offline SDK 6.45 outbound relay keepalive; no sender or response inference."""

import struct

from ._push_wire import seal_frame


def build_push_detect(*, push_id: int, access_id: int, sequence: int, tick_ms: int) -> bytes:
    """Encode iv_send_push_detect_frame's 44-byte client-originated variant.

    Supply the current channel sequence and an explicitly normalized 32-bit
    monotonic tick, not wall-clock time. This function owns no clock, increment,
    timer or retry. The checksum is not authentication; type-4 frames received
    from a relay do not necessarily have this same body interpretation.
    """
    for value, bits in ((push_id, 32), (access_id, 64), (sequence, 32), (tick_ms, 32)):
        if type(value) is not int or not 0 <= value < (1 << bits):
            raise ValueError("push detect field is out of range")
    frame = bytearray(44)
    frame[:2] = b"\x03\x04"
    struct.pack_into("<H", frame, 4, 24)
    struct.pack_into("<IQ", frame, 8, push_id, access_id)
    struct.pack_into("<IQ", frame, 24, sequence, tick_ms)
    return seal_frame(frame)
