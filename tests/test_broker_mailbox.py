from concurrent.futures import ThreadPoolExecutor

import pytest

from backend.app.drivers.yoosee.p2p.broker_mailbox import BrokerMailbox, MailboxTicket
from backend.app.drivers.yoosee.p2p.broker_reads import ReadCompletion, ReadRequest
from backend.app.drivers.yoosee.p2p.broker_transactions import ReceiptTicket
from backend.app.drivers.yoosee.p2p.contracts import ACCOUNT_IDENTITY_READ_PATHS, MODEL_READ_PATHS
from tests.test_broker_reads import DEVICE, PATH


def submit(box, timeout=2): return box.submit(DEVICE.device_id, PATH, timeout)


def completion(generation=1, device=DEVICE.device_id, path=PATH, sequence=40):
    request = ReadRequest(ReceiptTicket(generation, sequence, 0xB7), device, path)
    return ReadCompletion(request, "response", 0, 0, {"private": "value"})


def test_concurrent_admission_is_bounded_and_unique():
    box = BrokerMailbox(1)
    def attempt(_):
        try: return submit(box)
        except ValueError: return None
    with ThreadPoolExecutor(max_workers=4) as pool:
        accepted = [t for t in pool.map(attempt, range(16)) if t is not None]
    assert len(accepted) == len(set(accepted)) == 8
    work = box.poll()
    assert len(work.submissions) == 8 and not work.cancellations
    assert not box.poll().submissions
    for ticket in accepted:
        result = completion(sequence=40 + ticket.sequence)
        assert box.bind(ticket, result.request)
        assert box.finish(ticket, result)
    with pytest.raises(ValueError, match="capacity"): submit(box)
    assert box.take(accepted[0]).status == "response"
    assert box.take(accepted[0]) is None
    assert submit(box).sequence == 8


def test_cancelled_claimed_slots_remain_reserved_until_worker_observes_cancel():
    box = BrokerMailbox(1)
    tickets = [submit(box) for _ in range(8)]
    box.poll()
    for ticket in tickets:
        assert box.cancel(ticket)
        assert box.take(ticket).status == "cancelled"
        assert box.take(ticket) is None
        assert not box.finish(ticket, completion())
    with pytest.raises(ValueError, match="capacity"): submit(box)
    work = box.poll()
    assert work.cancellations == tuple(tickets) and not work.submissions
    with pytest.raises(ValueError, match="capacity"): submit(box)
    assert box.poll().cancellations == tuple(tickets)
    for ticket in tickets:
        assert box.acknowledge_cancel(ticket)
        assert not box.acknowledge_cancel(ticket)
    assert submit(box).sequence == 8


def test_expired_queued_requests_are_never_dispatched():
    now = [0.0]
    box = BrokerMailbox(1, clock=lambda: now[0])
    ticket = submit(box, 1)
    now[0] = 1
    assert not box.poll().submissions and not box.poll().cancellations
    assert box.take(ticket).status == "timeout"


def test_expired_claimed_requests_signal_cancel_and_late_response_is_discarded():
    now = [0.0]
    box = BrokerMailbox(1, clock=lambda: now[0])
    ticket = submit(box, 1)
    box.poll()
    now[0] = 1
    assert box.take(ticket).status == "timeout"
    assert not box.finish(ticket, completion())
    assert box.poll().cancellations == (ticket,)


def test_response_arriving_at_deadline_returns_timeout_without_retaining_payload():
    now = [0.0]
    box = BrokerMailbox(1, clock=lambda: now[0])
    ticket = submit(box, 1)
    box.poll()
    assert box.bind(ticket, completion().request)
    now[0] = 1
    assert box.finish(ticket, completion())
    result = box.take(ticket)
    assert result.status == "timeout" and result.completion is None
    assert not box.poll().cancellations


def test_shutdown_cancels_pending_but_preserves_already_completed_results():
    box = BrokerMailbox(1)
    first, second = submit(box), submit(box)
    box.poll()
    assert box.bind(first, completion().request)
    box.finish(first, completion())
    box.close()
    box.close()
    with pytest.raises(ValueError, match="closed"): submit(box)
    assert box.take(first).status == "response"
    assert box.take(second).status == "cancelled"
    assert box.poll().cancellations == (second,)
    assert not box.poll().submissions


@pytest.mark.parametrize("result", [completion(generation=2), completion(device=99), completion(path="wrong")])
def test_unrelated_completion_rejected(result):
    box = BrokerMailbox(1)
    ticket = submit(box)
    assert not box.finish(ticket, result)
    box.poll()
    assert not box.finish(ticket, result)
    assert box.take(ticket) is None
    assert not box.cancel(MailboxTicket(2, ticket.sequence))
    assert box.cancel(ticket)


def test_queued_cancellation_does_not_require_worker_slot():
    box = BrokerMailbox(1)
    ticket = submit(box)
    assert box.cancel(ticket)
    assert box.take(ticket).status == "cancelled"
    assert not box.poll().cancellations and not box.poll().submissions


def test_binding_prevents_cross_delivery_between_identical_reads():
    box = BrokerMailbox(1)
    first, second = submit(box), submit(box)
    assert not box.bind(first, completion().request)
    box.poll()
    assert not box.finish(first, completion())
    assert box.bind(first, completion().request)
    assert not box.bind(second, completion().request)
    other = completion(sequence=41)
    assert box.bind(second, other.request)
    assert not box.bind(first, other.request)
    assert not box.finish(first, other)
    assert not box.finish(second, completion())
    assert box.finish(second, other)
    assert box.finish(first, completion())


def test_cancellation_between_claim_and_bind_prevents_send():
    box = BrokerMailbox(1)
    ticket = submit(box)
    box.poll()
    assert box.cancel(ticket)
    assert not box.bind(ticket, completion().request)
    assert box.take(ticket).status == "cancelled"
    assert box.acknowledge_cancel(ticket)
    assert not box.poll().cancellations


@pytest.mark.parametrize("timeout", [True, 0, -1, float("nan"), float("inf"), 11, 10 ** 400])
def test_invalid_deadline_rejected(timeout):
    with pytest.raises(ValueError): submit(BrokerMailbox(1), timeout)


def test_ticket_exhaustion_does_not_wrap():
    box = BrokerMailbox(1)
    box._next = (1 << 64) - 1
    assert submit(box).sequence == (1 << 64) - 1
    with pytest.raises(ValueError, match="exhausted"): submit(box)


@pytest.mark.parametrize("path", sorted(MODEL_READ_PATHS - set(ACCOUNT_IDENTITY_READ_PATHS)))
def test_worker_does_not_inherit_the_full_property_reader_catalogue(path):
    box = BrokerMailbox(1)
    with pytest.raises(ValueError): box.submit(DEVICE.device_id, path)
    assert not box.poll().submissions


@pytest.mark.parametrize("path", ACCOUNT_IDENTITY_READ_PATHS)
def test_only_reviewed_identity_paths_admitted(path):
    box = BrokerMailbox(1)
    ticket = box.submit(DEVICE.device_id, path)
    assert box.poll().submissions[0].ticket == ticket
