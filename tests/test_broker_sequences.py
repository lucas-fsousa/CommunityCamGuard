import pytest

from backend.app.drivers.yoosee.p2p.broker_reads import BrokerReads
from backend.app.drivers.yoosee.p2p.broker_sequences import BrokerSequences
from backend.app.drivers.yoosee.p2p.broker_transactions import BrokerTransactions
from tests.test_broker_reads import DEVICE, NODE, PATH, response
from tests.test_broker_transactions import ack


def test_interleaved_consumers_share_one_sequence_without_recycling():
    source = BrokerSequences(NODE.session_id, 1, 40)
    reads = BrokerReads(NODE.session_id, 1, 40, sequences=source)
    receipts = BrokerTransactions(NODE.session_id, 1, 40, sequences=source)
    first = reads.register(DEVICE.device_id, PATH, 1)
    heartbeat_sequence = source.allocate()  # Reservation only, not heartbeat validation.
    other = receipts.register(0xB9, 1)
    assert (first.ticket.sequence, heartbeat_sequence, other.sequence) == (40, 41, 42)
    assert receipts.cancel(other)
    assert reads.accept(response(first), generation=1).request == first
    assert source.allocate() == 43
    reads.close()
    receipts.close()
    assert source.allocate() == 44  # Consumer cleanup cannot close the account namespace.


def test_retirement_blocks_late_responses_and_new_work_but_preserves_cancellations():
    source = BrokerSequences(NODE.session_id, 1, 40)
    reads = BrokerReads(NODE.session_id, 1, 40, sequences=source)
    receipts = BrokerTransactions(NODE.session_id, 1, 40, sequences=source)
    request = reads.register(DEVICE.device_id, PATH, 1)
    ticket = receipts.register(0xB9, 1)
    source.close()
    source.close()
    assert reads.accept(response(request), generation=1) is None
    assert receipts.accept(ack(ticket.sequence, session=NODE.session_id), generation=1) is None
    with pytest.raises(ValueError, match="closed"):
        reads.register(DEVICE.device_id, PATH, 1)
    with pytest.raises(ValueError, match="closed"):
        source.allocate()
    assert reads.close()[0].request == request
    assert receipts.close() == (ticket,)


@pytest.mark.parametrize("session,generation", [(1, 1), (NODE.session_id, 2), (True, 1), (NODE.session_id, True)])
def test_shared_identity_mismatch_rejected(session, generation):
    source = BrokerSequences(NODE.session_id, 1, 40)
    with pytest.raises(ValueError):
        BrokerTransactions(session, generation, 40, sequences=source)
    assert source.allocate() == 40


def test_shared_exhaustion_never_wraps_or_closes_other_waiters():
    source = BrokerSequences(NODE.session_id, 1, 0xFFFFFFFF)
    reads = BrokerReads(NODE.session_id, 1, 0, sequences=source)
    request = reads.register(DEVICE.device_id, PATH, 1)
    with pytest.raises(ValueError, match="exhausted"):
        source.allocate()
    assert reads.accept(response(request), generation=1).status == "response"
    with pytest.raises(ValueError, match="exhausted"):
        reads.register(DEVICE.device_id, PATH, 1)


def test_invalid_or_full_registration_does_not_consume_sequence():
    source = BrokerSequences(NODE.session_id, 1, 40)
    owner = BrokerTransactions(NODE.session_id, 1, 40, sequences=source)
    with pytest.raises(ValueError):
        owner.register(0xB7, float("nan"))
    assert source.allocate() == 40
    for _ in range(8):
        owner.register(0xB7, 1)
    with pytest.raises(ValueError, match="capacity"):
        owner.register(0xB7, 1)
    assert source.allocate() == 49
