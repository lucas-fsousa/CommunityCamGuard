"""Socket-free periodic TCP relay measurement, distinct from relay pairing."""

import struct

from .media_protocol import build_mtp_frame, verify_mtp_frame


def matches_mtp_tcp_measurement_ack(
    frame: bytes, *, expected_link_id: int, expected_source_id: int,
    expected_destination_id: int, expected_sequence: int, expected_timestamp_ms: int,
) -> bool:
    """Match the observed 86-byte relay ACK, not general channel readiness.

    Checksum covers only 24 payload bytes, not the tail. Neither it nor session
    correlation constitutes cryptographic authentication. The
    caller must validate endpoint provenance and own the outstanding request.
    Only the measured c0/d0, 72-byte record is accepted; no transport fallback.
    """
    expected = ((expected_link_id, 32), (expected_source_id, 64),
                (expected_destination_id, 64), (expected_sequence, 32),
                (expected_timestamp_ms, 64))
    if any(type(value) is not int or not 0 <= value < (1 << width)
           for value, width in expected):
        return False
    if len(frame) != 86 or frame[:2] != b"\xc0\xd0" or not verify_mtp_frame(frame):
        return False
    body = frame[14:]
    return (
        body[:4] == b"\x00\x02\x44\x00"
        and struct.unpack_from("<I", body, 8)[0] == 0
        and struct.unpack_from("<I", body, 52)[0] == 72
        and struct.unpack_from("<I", body, 4)[0] == expected_link_id
        and struct.unpack_from("<QQIQ", body, 12) == (
            expected_source_id, expected_destination_id,
            expected_sequence, expected_timestamp_ms,
        )
    )


def build_mtp_tcp_measurement(
    *, relay_link_id: int, source_id: int, destination_id: int, call_id: int,
    sequence: int, timestamp_ms: int, session_role: int, channel_type: int = 0x86,
) -> bytes:
    """Encode SDK 6.45 send_meter_frm plus its TCP relay transport wrapper.

    Caller supplies current session fields, not a cached pairing packet. Native
    channels 0x85/0x86 select plain/extended TCP envelopes; the *body* channel
    stays zero for both. This does not schedule, connect, start AV or establish
    readiness. session_role is the byte at native session +0x8ac.
    """
    fields = ((relay_link_id, 32), (source_id, 64), (destination_id, 64),
              (call_id, 32), (sequence, 32), (timestamp_ms, 64), (session_role, 8))
    if any(type(value) is not int or not 0 <= value < (1 << width)
           for value, width in fields):
        raise ValueError("TCP measurement field is outside its unsigned wire width")
    if type(channel_type) is not int or channel_type not in (0x85, 0x86):
        raise ValueError("Only the mapped TCP relay channels are supported")
    body = bytearray(72)
    body[1] = 1
    struct.pack_into("<HIIQQIQ", body, 2, 68, relay_link_id, 8,
                     source_id, destination_id, sequence, timestamp_ms)
    struct.pack_into("<I", body, 52, 72)
    body[64] = 2
    body[65] = session_role
    struct.pack_into("<I", body, 68, call_id)
    if channel_type == 0x86:
        return build_mtp_frame(0xE0, struct.pack("<Q", destination_id) + body)
    return build_mtp_frame(0x80, bytes(body))
