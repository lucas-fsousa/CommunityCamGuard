"""Offline SDK-shaped TCP pairing request; no connector or readiness inference."""

import struct
from dataclasses import dataclass

from .media_protocol import build_mtp_frame, verify_mtp_frame


@dataclass(frozen=True, slots=True)
class MTPTCPMeter:
    kind: int
    timestamp_ms: int


def parse_mtp_tcp_meter(
    frame: bytes, *, expected_link_id: int, expected_source_id: int,
    expected_destination_id: int,
) -> MTPTCPMeter | None:
    """Correlate the observed extended TCP meter; never implies media readiness.

    SDK dispatch skips eight bytes when prefix bits 5–6 are nonzero. Accept only
    the observed c0/d0, 68-byte body here. The extension remains uninterpreted.
    Checksum/correlation are not cryptographic authentication: the caller owns
    broker provenance and the connected endpoint. Kind 1 is a request, not ACK.
    """
    if len(frame) != 82 or frame[:2] != b"\xc0\xd0" or not verify_mtp_frame(frame):
        return None
    body = frame[14:]
    if body[0] != 0 or body[1] not in (1, 2) or struct.unpack_from("<H", body, 2)[0] != 68:
        return None
    if (struct.unpack_from("<I", body, 4)[0] != expected_link_id
            or struct.unpack_from("<Q", body, 12)[0] != expected_source_id
            or struct.unpack_from("<Q", body, 20)[0] != expected_destination_id):
        return None
    return MTPTCPMeter(body[1], struct.unpack_from("<Q", body, 32)[0])


def build_mtp_tcp_pair_request(
    *, relay_link_id: int, source_id: int, destination_id: int, timestamp_ms: int,
) -> bytes:
    """Encode the 74-byte request from SDK 6.45 iv_on_tcp_connect_finished.

    relay_link_id comes from native MTP session +0x5e8. A newly allocated SDK
    session copies it to the calling link at +0x20; reused sessions need their
    own provenance rather than assuming those fields stay equal indefinitely.
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
