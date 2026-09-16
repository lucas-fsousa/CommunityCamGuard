"""Fixed, non-identifying labels for already route-matched MTP measurements."""

import struct

from .media_protocol import MediaMeter


def classify_meter(parsed: MediaMeter, wire: bytes, call_id: int,
                   sent: set[tuple[int, int]]) -> set[str]:
    """No payload, identifiers, sequence values or timestamps leave this helper."""
    labels = {{1: "request", 2: "reply"}.get(parsed.kind, "unknown_kind")}
    if parsed.channel_type != 4:
        labels.add("wrong_channel")
    if parsed.record_length != len(wire) - 6:
        labels.add("wrong_record_length")
    if parsed.role not in (1, 2, 3):
        labels.add("wrong_role")
    if parsed.call_id not in (None, call_id):
        labels.add("wrong_call")
    if parsed.kind == 2:
        if not any(sequence == parsed.sequence for sequence, _ in sent):
            labels.add("unsent_sequence")
        if (struct.unpack_from("<Q", wire, 38)[0] != parsed.timestamp
                or (parsed.sequence, parsed.timestamp) not in sent):
            labels.add("unmatched_timestamp")
    return labels
