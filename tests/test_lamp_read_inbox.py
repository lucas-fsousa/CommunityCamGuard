import pytest

from backend.app.drivers.yoosee.p2p.lamp_control_specs import LampWriteSpec
from backend.app.drivers.yoosee.p2p.lamp_read_inbox import LampReadInbox, LampReadTicket
from backend.app.drivers.yoosee.p2p.lamp_read_job import LampReadResult, LampReadSpec

SPEC = LampReadSpec(123, 7000000002)


def test_one_slot_includes_unread_result_and_cancellation_frees_only_matching_ticket():
    inbox = LampReadInbox(1)
    ticket = inbox.submit(SPEC)
    with pytest.raises(ValueError): inbox.submit(SPEC)
    assert inbox.claim()[:2] == (ticket, SPEC)
    assert inbox.claim() is None
    inbox.cancel(LampReadTicket(2, ticket.serial))
    assert not inbox.should_cancel(ticket)
    inbox.finish(ticket, LampReadResult("response", False))
    with pytest.raises(ValueError): inbox.submit(SPEC)
    assert inbox.take(ticket).enabled is False
    later = inbox.submit(SPEC)
    inbox.cancel(ticket)
    assert not inbox.should_cancel(later)
    inbox.cancel(later)
    assert inbox.should_cancel(later)
    assert inbox.submit(SPEC).serial > later.serial


def test_queued_deadline_never_extends_and_close_preserves_completed_read():
    now = [0.0]
    inbox = LampReadInbox(1, clock=lambda: now[0])
    first = inbox.submit(SPEC)
    now[0] = 2
    assert inbox.claim() is None and inbox.take(first).status == "timeout"
    second = inbox.submit(SPEC)
    inbox.claim()
    inbox.finish(second, LampReadResult("response", True))
    inbox.close()
    assert inbox.take(second).enabled is True
    with pytest.raises(ValueError): inbox.submit(SPEC)


def test_close_cancels_pending_read_without_requiring_camera_stop():
    inbox = LampReadInbox(1)
    ticket = inbox.submit(SPEC)
    inbox.claim()
    inbox.close()
    assert inbox.take(ticket).status == "cancelled"


@pytest.mark.parametrize("spec", [LampReadSpec(True, 1), LampReadSpec(1, 0), LampReadSpec(1, True)])
def test_invalid_targets_cannot_enter_inbox(spec):
    with pytest.raises(ValueError): LampReadInbox(1).submit(spec)


def test_claimed_cancelled_write_holds_both_read_and_write_admission_until_owner_retires():
    inbox=LampReadInbox(1); spec=LampWriteSpec(123,7000000002,True)
    with pytest.raises(ValueError): inbox.submit(spec)
    with pytest.raises(ValueError): inbox.submit_write(SPEC)
    ticket=inbox.submit_write(spec); assert inbox.claim()[0]==ticket
    inbox.cancel(LampReadTicket(2,ticket.serial));assert not inbox.should_cancel(ticket)
    inbox.cancel(ticket);assert inbox.should_cancel(ticket) and inbox.take(ticket) is None
    with pytest.raises(ValueError): inbox.submit(SPEC)
    with pytest.raises(ValueError): inbox.submit_write(spec)
    inbox.finish(ticket,LampReadResult('cancelled',sent=True))
    assert inbox.submit(SPEC).serial > ticket.serial


def test_cancelling_completed_unread_write_releases_slot_without_waiting_for_an_absent_job():
    inbox=LampReadInbox(1)
    ticket=inbox.submit_write(LampWriteSpec(123,7000000002,True));inbox.claim()
    inbox.finish(ticket,LampReadResult('response',error_code=0,sent=True))
    inbox.cancel(ticket)
    assert inbox.take(ticket) is None
    assert inbox.submit(SPEC).serial>ticket.serial
