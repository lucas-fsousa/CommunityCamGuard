import struct

import pytest

from backend.app.drivers.yoosee.p2p.broker_heartbeat_reply import match_heartbeat_reply
from backend.app.drivers.yoosee.p2p.broker_transactions import ReceiptTicket
from backend.app.drivers.yoosee.p2p.wire import new_header

TICKET = ReceiptTicket(1, 17, 0xA0)


def reply(*, sequence=17, outer_sequence=900, hint=50, flags=(2 << 16) | (1 << 21), size=40):
    frame = new_header(0xA1, size, 9, outer_sequence, flags)
    frame.extend(bytes(max(0, size - len(frame))))
    struct.pack_into("<I", frame, 16, sequence)
    frame[0x21] = hint
    return bytes(frame)


@pytest.mark.parametrize("hint,expected", [(0, None), (9, None), (10, 10), (25, 25),
    (35, 35), (40, 40), (50, 50), (60, 60), (61, None), (255, None)])
def test_correlates_original_sequence_and_preserves_valid_hint(hint, expected):
    result = match_heartbeat_reply(reply(hint=hint), TICKET, generation=1, session_id=9)
    assert result.ticket == TICKET and result.interval_hint == expected


@pytest.mark.parametrize("frame,generation,session,ticket", [
    (reply(sequence=900, outer_sequence=17), 1, 9, TICKET),
    (reply(), 2, 9, TICKET), (reply(), True, 9, TICKET), (reply(), 1, 10, TICKET),
    (reply(), 1, 9, ReceiptTicket(1, 17, 0xB7)),
    (reply(flags=2 << 16), 1, 9, TICKET),
    (reply(flags=(2 << 16) | (1 << 21) | (1 << 20)), 1, 9, TICKET),
    (reply(flags=(1 << 16) | (1 << 21)), 1, 9, TICKET),
    (reply()[:-1], 1, 9, TICKET), (reply() + b"x", 1, 9, TICKET),
    (reply(size=4097), 1, 9, TICKET), (b"", 1, 9, TICKET),
])
def test_malformed_or_unrelated_does_not_match(frame, generation, session, ticket):
    assert match_heartbeat_reply(frame, ticket, generation=generation, session_id=session) is None


@pytest.mark.parametrize("offset", [0, 1, 2, 4, 16])
def test_header_corruption_rejected(offset):
    frame = bytearray(reply())
    frame[offset] ^= 2
    assert match_heartbeat_reply(bytes(frame), TICKET, generation=1, session_id=9) is None
