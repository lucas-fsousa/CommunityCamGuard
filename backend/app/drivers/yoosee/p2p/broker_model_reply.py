"""Correlate complete GDM read replies; never confuse ACKs/reports with results."""

import struct
from dataclasses import dataclass, field

from .broker_transactions import ReceiptTicket
from .model_protocol import parse_model_read_response

MAX_REPLY_BYTES = 4096


def _bounded_value(value: object) -> bool:
    pending = [(value, 0)]
    visited = 0
    while pending:
        item, depth = pending.pop()
        visited += 1
        if depth > 16 or visited > 256:
            return False
        children = list(item.values()) if isinstance(item, dict) else item if isinstance(item, list) else ()
        if len(pending) + len(children) > 256:
            return False
        pending.extend((child, depth + 1) for child in children)
    return True


@dataclass(frozen=True, slots=True)
class CorrelatedModelReply:
    ticket: ReceiptTicket
    error_code: int
    value: object = field(repr=False)


def match_model_reply(plain: bytes, ticket: ReceiptTicket, *, generation: int,
                      session_id: int, device_id: int) -> CorrelatedModelReply | None:
    """Caller verifies broker peer/decryption and that ticket is live/outstanding.

    B8 bit 21 overloads offset 0x10 with the original request sequence, not a
    checksum. Reuse the reviewed codec; never apply transport-ACK checksum rules
    or match the independent outer response sequence. No payload is retained.
    """
    if (type(generation) is not int or generation != ticket.generation or ticket.kind != 0xB7
            or not isinstance(plain, bytes) or not 0x26 <= len(plain) <= MAX_REPLY_BYTES
            or struct.unpack_from("<Q", plain, 4)[0] != session_id
            or ((struct.unpack_from("<I", plain, 20)[0] >> 16) & 3) != 2):
        return None
    try:
        parsed = parse_model_read_response(plain, device_id, request_sequence=ticket.sequence)
    except (ValueError, RecursionError):
        return None
    if parsed is None:
        return None
    error, value = parsed
    if not _bounded_value(value):
        return None
    return CorrelatedModelReply(ticket, error, value)
