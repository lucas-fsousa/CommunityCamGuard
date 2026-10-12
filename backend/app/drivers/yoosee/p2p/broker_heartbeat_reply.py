"""Bounded A1 matching, separate from same-kind transport acknowledgements."""

import struct
from dataclasses import dataclass

from .broker_transactions import ReceiptTicket


@dataclass(frozen=True, slots=True)
class HeartbeatReply:
    ticket: ReceiptTicket
    interval_hint: int | None


def match_heartbeat_reply(plain: bytes, ticket: ReceiptTicket, *, generation: int,
                          session_id: int) -> HeartbeatReply | None:
    """Caller checks broker peer/decryption/reassembly and outstanding deadline.

    SDK response dispatch matches original request sequence at 0x10, not the
    outer response sequence at 0x0c. Unknown optional fields are not interpreted.
    The 40-byte fixed header is sufficient for the interval hint at 0x21.
    """
    if (type(generation) is not int or generation != ticket.generation or ticket.kind != 0xA0
            or not isinstance(plain, bytes) or not 40 <= len(plain) <= 4096
            or plain[0] not in (0x7E, 0x7F) or plain[1] != 0xA1
            or struct.unpack_from("<H", plain, 2)[0] != len(plain)
            or struct.unpack_from("<Q", plain, 4)[0] != session_id
            or struct.unpack_from("<I", plain, 16)[0] != ticket.sequence):
        return None
    flags = struct.unpack_from("<I", plain, 20)[0]
    if ((flags >> 16) & 3) != 2 or not flags & (1 << 21) or flags & (1 << 20):
        return None
    hint = plain[0x21]
    return HeartbeatReply(ticket, hint if 10 <= hint <= 60 else None)
