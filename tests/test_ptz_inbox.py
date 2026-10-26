from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace

import pytest

from backend.app.drivers.yoosee.p2p.ptz_inbox import MotionTicket, PtzInbox
from backend.app.drivers.yoosee.p2p.ptz_scheduler import PtzSpec, ScheduledPtzResult

SPEC = PtzSpec(123, 456, "right", frozenset({"right"}))
SUCCESS = ScheduledPtzResult("released", True, 1, True, False)


def test_one_slot_includes_unread_result_and_claim_is_once():
    inbox = PtzInbox(1)
    ticket = inbox.submit(SPEC)
    assert inbox.claim() == (ticket, SPEC) and inbox.claim() is None
    assert inbox.finish(ticket, SUCCESS)
    with pytest.raises(ValueError): inbox.submit(SPEC)
    assert inbox.take(ticket) == SUCCESS and inbox.take(ticket) is None
    assert inbox.submit(SPEC).serial != ticket.serial


def test_cancelled_claim_waits_for_worker_stop_before_freeing_slot():
    inbox = PtzInbox(1)
    ticket = inbox.submit(SPEC)
    inbox.claim()
    assert inbox.cancel(ticket) and inbox.should_cancel(ticket)
    assert inbox.take(ticket) is None
    with pytest.raises(ValueError): inbox.submit(SPEC)
    inbox.finish(ticket, SUCCESS)
    assert inbox.take(ticket) == SUCCESS


def test_abandoned_caller_frees_slot_only_after_confirmed_stop():
    inbox = PtzInbox(1)
    ticket = inbox.submit(SPEC)
    inbox.claim()
    inbox.abandon(ticket)
    assert inbox.should_cancel(ticket)
    with pytest.raises(ValueError): inbox.submit(SPEC)
    assert inbox.finish(ticket, SUCCESS)
    current = inbox.submit(SPEC)
    inbox.abandon(ticket)
    assert not inbox.should_cancel(current)


def test_abandoned_unknown_stop_keeps_inbox_poisoned():
    inbox = PtzInbox(1)
    ticket = inbox.submit(SPEC)
    inbox.claim()
    inbox.abandon(ticket)
    inbox.finish(ticket, ScheduledPtzResult("release_unconfirmed", True, 3, False, False))
    with pytest.raises(ValueError): inbox.submit(SPEC)
    assert not inbox.take(ticket).release_delivery_confirmed
    with pytest.raises(ValueError): inbox.submit(SPEC)


def test_abandoning_before_claim_cannot_create_a_move_or_leave_a_stuck_slot():
    inbox = PtzInbox(1)
    old = inbox.submit(SPEC)
    inbox.abandon(old)
    assert inbox.claim() is None
    assert inbox.submit(SPEC).serial > old.serial


@pytest.mark.parametrize("mode", ["cancel", "expire", "close"])
def test_unclaimed_job_cancels_without_motion(mode):
    now = [0.0]
    inbox = PtzInbox(1, clock=lambda: now[0])
    ticket = inbox.submit(SPEC)
    if mode == "cancel": inbox.cancel(ticket)
    if mode == "close": inbox.close()
    if mode == "expire": now[0] = 1
    assert inbox.claim() is None
    result = inbox.take(ticket)
    assert result.reason == "cancelled" and not result.start_attempted


def test_old_ticket_cannot_cancel_or_finish_replacement():
    inbox = PtzInbox(1)
    old = inbox.submit(SPEC)
    inbox.cancel(old)
    inbox.take(old)
    current = inbox.submit(SPEC)
    inbox.claim()
    for stale in (old, MotionTicket(2, current.serial)):
        assert not inbox.cancel(stale) and not inbox.finish(stale, SUCCESS)
        assert inbox.take(stale) is None
    assert not inbox.should_cancel(current)


def test_unknown_stop_permanently_closes_admission_even_after_result_consumed():
    inbox = PtzInbox(1)
    ticket = inbox.submit(SPEC)
    inbox.claim()
    unknown = ScheduledPtzResult("release_unconfirmed", True, 3, False, False)
    inbox.finish(ticket, unknown)
    assert inbox.take(ticket) == unknown
    with pytest.raises(ValueError): inbox.submit(SPEC)


def test_close_latches_cancel_without_discarding_inflight_result():
    inbox = PtzInbox(1)
    ticket = inbox.submit(SPEC)
    inbox.claim()
    inbox.close()
    assert inbox.should_cancel(ticket) and inbox.take(ticket) is None
    assert inbox.finish(ticket, SUCCESS) and inbox.take(ticket) == SUCCESS
    with pytest.raises(ValueError): inbox.submit(SPEC)


def test_concurrent_submit_has_exactly_one_winner():
    inbox = PtzInbox(1)
    def submit(_):
        try: return inbox.submit(SPEC)
        except ValueError: return None
    with ThreadPoolExecutor(max_workers=2) as pool:
        tickets = list(pool.map(submit, range(8)))
    assert sum(ticket is not None for ticket in tickets) == 1


@pytest.mark.parametrize("changes", [dict(access_id=True), dict(device_id=0),
    dict(direction="diagonal"), dict(reviewed_directions=frozenset({"left"})),
    dict(reviewed_directions={"right"}), dict(duration=float("nan")), dict(duration=1)])
def test_invalid_admission_does_not_occupy_slot(changes):
    inbox = PtzInbox(1)
    with pytest.raises(ValueError): inbox.submit(replace(SPEC, **changes))
    assert inbox.submit(SPEC).serial == 0
