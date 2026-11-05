"""Receive-only KCP window report; no sender, queue, timer or socket ownership."""

from .media_protocol import KCP_HEADER, KCP_WINS, build_mtp_frame


def build_window_report(conv: int, *, unacknowledged: int, window: int) -> bytes:
    if any(type(value) is not int or not 0 <= value <= maximum for value, maximum in (
        (conv, 0xFFFFFFFF), (unacknowledged, 0xFFFFFFFF), (window, 0xFFFF),
    )):
        raise ValueError("invalid KCP window report")
    return build_mtp_frame(0x10, KCP_HEADER.pack(
        conv, KCP_WINS, 0, window, 0, 0, unacknowledged, 0,
    ))
