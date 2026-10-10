"""Socket-free ACK for the reviewed extended TCP meter request only."""

import struct

from .media_protocol import build_mtp_frame, parse_media_meter
from .mtp_tcp_handshake import parse_mtp_tcp_meter


def build_mtp_tcp_meter_ack(
    frame: bytes, *, expected_link_id: int, expected_source_id: int,
    expected_destination_id: int,
) -> bytes:
    """Reply to c0/d0 with c0/e0 per SDK iv_rcv_meter_req's TCP mode-2 path.

    Caller owns the current authenticated route and TCP socket. Do not send to
    the address embedded in the received extension. A send is not readiness.
    Extended record bodies beyond the observed 68-byte layout stay unsupported.
    """
    meter = parse_mtp_tcp_meter(
        frame, expected_link_id=expected_link_id, expected_source_id=expected_source_id,
        expected_destination_id=expected_destination_id,
    )
    if meter is None or meter.kind != 1:
        raise ValueError("not a correlated extended TCP meter request")
    request = frame[14:]
    return _reply(request, extended=True)


def build_mtp_tcp_plain_meter_ack(
    frame: bytes, *, expected_link_id: int, expected_source_id: int,
    expected_destination_id: int,
) -> bytes:
    """Reply on the same TCP socket to the observed 74-byte c0/90 request."""
    meter = parse_media_meter(frame)
    if (len(frame) != 74 or meter is None or meter.kind != 1
            or meter.link_id != expected_link_id or meter.source_id != expected_source_id
            or meter.destination_id != expected_destination_id or frame[6] != 0):
        raise ValueError("not a correlated plain TCP meter request")
    return _reply(frame[6:], extended=False)


def _reply(request: bytes, *, extended: bool) -> bytes:
    record_length = struct.unpack_from("<I", request, 52)[0]
    if record_length > 68 or struct.unpack_from("<I", request, 8)[0] & 0x40:
        raise ValueError("extended TCP meter body is not supported")
    body = bytearray(68)
    body[1] = 2
    struct.pack_into("<H", body, 2, 68)
    body[4:8] = request[4:8]
    body[12:20], body[20:28] = request[20:28], request[12:20]
    body[28:40] = request[28:40]  # Sequence and complete 64-bit timestamp.
    body[48:52] = request[48:52]
    struct.pack_into("<I", body, 52, 68)
    body[64] = 2
    # SDK routes the ACK to the request source, not its opaque input extension.
    if extended:
        return build_mtp_frame(0xE0, request[12:20] + bytes(body))
    channel = struct.unpack_from("<I", request, 48)[0]
    return build_mtp_frame(0x90 if channel in (1, 2) else 0x80, bytes(body))
