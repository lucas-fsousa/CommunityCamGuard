"""Offline SDK-shaped TCP pairing request; no connector or readiness inference."""

import struct

from .media_protocol import build_mtp_frame


def build_mtp_tcp_pair_request(
    *, relay_link_id: int, source_id: int, destination_id: int, timestamp_ms: int,
) -> bytes:
    """Encode the 74-byte request from SDK 6.45 iv_on_tcp_connect_finished.

    relay_link_id comes from native MTP session +0x5e8, not automatically the
    calling link at +0x20. Its assignment must be established before live use.
    The timestamp is supplied by the caller, in monotonic milliseconds. No AV
    command, token, userdata, retry, session lookup or network I/O is added.
    """
    for value, width in ((relay_link_id, 32), (source_id, 64),
                         (destination_id, 64), (timestamp_ms, 64)):
        if type(value) is not int or not 0 <= value < (1 << width):
            raise ValueError("TCP pairing field is outside its unsigned wire width")
    payload = bytearray(68)
    payload[1] = 1
    struct.pack_into("<H", payload, 2, 68)
    struct.pack_into("<I", payload, 4, relay_link_id)
    struct.pack_into("<Q", payload, 12, source_id)
    struct.pack_into("<Q", payload, 20, destination_id)
    struct.pack_into("<Q", payload, 32, timestamp_ms)
    return build_mtp_frame(0x80, bytes(payload))
