"""Fixed, non-identifying labels for already route-matched MTP measurements."""

import struct

from .media_protocol import MediaMeter


def has_reply_only_extension(parsed: MediaMeter, wire: bytes) -> bool:
    """SDK zero-filled reply tail, or the captured 68-byte revision marker.

    These are reply layouts, not role zero / call zero wildcards for requests.
    SDK iv_rcv_meter_req zeroes its output and does not copy request extensions.
    """
    return parsed.kind == 2 and wire[70:] in (bytes(4), bytes(8), b"\x02\x00\x00\x00")


def classify_meter(parsed: MediaMeter, wire: bytes, call_id: int,
                   sent: set[tuple[int, int]]) -> set[str]:
    """No payload, identifiers, sequence values or timestamps leave this helper."""
    labels = {{1: "request", 2: "reply"}.get(parsed.kind, "unknown_kind")}
    if parsed.channel_type != 4:
        labels.add("wrong_channel")
    if parsed.record_length != len(wire) - 6:
        labels.add("wrong_record_length")
    reply_extension = has_reply_only_extension(parsed, wire)
    if parsed.role not in (1, 2, 3) and not reply_extension:
        labels.add("wrong_role")
    if parsed.call_id not in (None, call_id) and not reply_extension:
        labels.add("wrong_call")
    if parsed.kind == 2:
        if not any(sequence == parsed.sequence for sequence, _ in sent):
            labels.add("unsent_sequence")
        if (struct.unpack_from("<Q", wire, 38)[0] != parsed.timestamp
                or (parsed.sequence, parsed.timestamp) not in sent):
            labels.add("unmatched_timestamp")
    return labels
