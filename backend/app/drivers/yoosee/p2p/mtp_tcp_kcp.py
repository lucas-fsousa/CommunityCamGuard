"""Socket-free adaptation between canonical KCP frames and TCP relay envelopes.

No channel selection, session admission, retransmission or AV startup lives here.
The caller owns the connected peer, current conversation and complete TCP record.
"""

import struct

from .media_protocol import build_mtp_frame, parse_kcp_segments, verify_mtp_frame

TCP_RECORD_LIMIT = 1500  # SDK TCP receive callback rejects larger records.


def _validate_conversation(frame: bytes, conversation: int) -> None:
    if type(conversation) is not int or not 0 <= conversation <= 0xFFFFFFFF:
        raise ValueError("invalid KCP conversation")
    segments = parse_kcp_segments(frame)
    if any(segment.conv != conversation for segment in segments):
        raise ValueError("KCP record belongs to a different conversation")


def wrap_tcp_relay_kcp(
    frame: bytes, *, destination_id: int, expected_conversation: int,
    channel_type: int = 0x86,
) -> bytes:
    """Wrap canonical c0/10 bytes with SDK channel 0x85/0x86 TCP framing.

    KCP bytes are unchanged, including sequence/timestamp on retransmission.
    The 0x86 route prefix is the destination ID, not an opaque received prefix.
    """
    if type(destination_id) is not int or not 0 <= destination_id < (1 << 64):
        raise ValueError("invalid TCP relay destination")
    if type(channel_type) is not int or channel_type not in (0x85, 0x86):
        raise ValueError("unsupported TCP relay channel")
    if len(frame) + (8 if channel_type == 0x86 else 0) > TCP_RECORD_LIMIT:
        raise ValueError("TCP relay record exceeds receive limit")
    _validate_conversation(frame, expected_conversation)
    payload = frame[6:]
    if channel_type == 0x86:
        return build_mtp_frame(0x60, struct.pack("<Q", destination_id) + payload)
    return build_mtp_frame(0, payload)


def unwrap_tcp_relay_kcp(frame: bytes, *, expected_conversation: int) -> bytes:
    """Normalize mapped inbound mode 0/2 KCP envelopes after wire validation.

    Mode-2 prefix bytes remain opaque. Endpoint ownership and exact conversation
    are mandatory; this checksum is partial and is not authentication. Outbound
    c0/60 and MTP meter frames are not accepted as inbound KCP data.
    """
    if (len(frame) > TCP_RECORD_LIMIT or frame[:2] not in (b"\xc0\x10", b"\xc0\x50")
            or not verify_mtp_frame(frame)):
        raise ValueError("invalid inbound TCP relay KCP record")
    offset = 14 if frame[1] == 0x50 else 6
    # Verify the original envelope first: recomputing a normalized checksum must
    # never hide a damaged transport prefix. KCP structure is checked afterward.
    normalized = build_mtp_frame(0x10, frame[offset:])
    _validate_conversation(normalized, expected_conversation)
    return normalized
