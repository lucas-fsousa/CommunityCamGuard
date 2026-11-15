"""Caller departure must not leak slots or permit overlapping microphone owners."""

from types import SimpleNamespace

import pytest

from backend.app.drivers.contracts import ControlOperationError
from backend.app.drivers.yoosee import shared_talk
from backend.app.drivers.yoosee.p2p.talk_inbox import TalkInbox, TalkOutcome, TalkTicket
from tests.test_av_shared_talk import FRAME


def test_queued_abandon_frees_slot_without_any_claim_or_action():
    inbox = TalkInbox(2, 1)
    ticket = inbox.submit(7, (FRAME,))
    inbox.abandon(ticket)
    assert inbox.claim() is None and inbox.take(ticket) is None
    assert inbox.submit(7, (FRAME,)) != ticket


def test_claimed_abandon_waits_for_owned_cleanup_then_frees_unread_slot():
    inbox = TalkInbox(2, 1)
    ticket = inbox.submit(7, (FRAME,))
    assert inbox.claim()[0] == ticket
    inbox.abandon(ticket)
    assert inbox.should_cancel(ticket)
    with pytest.raises(ValueError, match="slot"):
        inbox.submit(7, (FRAME,))
    inbox.finish(ticket, TalkOutcome("cancelled", 0, True))
    assert inbox.take(ticket) is None
    assert inbox.submit(7, (FRAME,)) != ticket


def test_stale_abandon_cannot_discard_new_generation_or_replacement():
    inbox = TalkInbox(2, 1)
    ticket = inbox.submit(7, (FRAME,))
    inbox.abandon(TalkTicket(1, ticket.serial))
    assert not inbox.should_cancel(ticket)
    inbox.abandon(ticket)
    replacement = inbox.submit(7, (FRAME,))
    inbox.abandon(ticket)
    assert not inbox.should_cancel(replacement)


def test_abandon_discards_an_already_finished_unread_result():
    inbox = TalkInbox(2, 1)
    ticket = inbox.submit(7, (FRAME,))
    inbox.claim()
    inbox.finish(ticket, TalkOutcome("transport_received", 1, True))
    inbox.abandon(ticket)
    assert inbox.take(ticket) is None
    assert inbox.submit(7, (FRAME,)) != ticket


def test_expired_pcm_adapter_does_not_leave_a_permanently_busy_slot(monkeypatch):
    inbox = TalkInbox(2, 1)
    session = SimpleNamespace(submit_talk=lambda frames: inbox.submit(7, frames),
        take_talk=inbox.take, cancel_talk=inbox.cancel, abandon_talk=inbox.abandon)
    ticks = iter((0, 19))
    monkeypatch.setattr(shared_talk, "time", SimpleNamespace(monotonic=lambda: next(ticks)))
    monkeypatch.setattr(shared_talk, "session_for", lambda entry: session)
    monkeypatch.setattr(shared_talk, "encode_pcm16le", lambda pcm: (FRAME,))
    with pytest.raises(ControlOperationError, match="timed out"):
        shared_talk.send_candidate(object(), bytes(640))
    assert inbox.claim() is None
    inbox.submit(7, (FRAME,))  # Regression: cancel left an unread result occupying this slot.
