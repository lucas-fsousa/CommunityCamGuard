"""Typed shared-account audio submission with synthetic transport only."""

import pytest

from backend.app.drivers.yoosee.p2p.account_media_channel import MediaBootstrap
from backend.app.drivers.yoosee.p2p.media_lease_inbox import MediaLeaseInbox
from backend.app.drivers.yoosee.p2p.media_lease_pump import MediaLeasePump
from backend.app.drivers.yoosee.p2p.talk_inbox import TalkInbox, TalkTicket
from tests.test_account_media_route import SPEC
from tests.test_av_handshake import ack
from tests.test_av_shared_talk import FRAME
from tests.test_cooperative_udp_media import negotiate, session
from tests.test_media_lease_inbox import TARGETS, bootstrap, camera


def test_submission_requires_a_claimed_exact_live_camera_lease():
    inbox = MediaLeaseInbox(1, 123, targets=TARGETS)
    media = bootstrap(0)
    lease = inbox.submit(camera(0), media)
    with pytest.raises(ValueError, match="exact-camera"):
        inbox.submit_talk(camera(0), (FRAME,))
    inbox.claim()
    with pytest.raises(ValueError, match="exact-camera"):
        inbox.submit_talk(camera(1), (FRAME,))
    ticket = inbox.submit_talk(camera(0), (FRAME,))
    assert inbox.talk.claim() == (ticket, lease.serial, (FRAME,))
    inbox.cancel(lease)
    with pytest.raises(ValueError, match="exact-camera"):
        inbox.submit_talk(camera(0), (FRAME,))


def test_duplicate_unread_and_stale_requests_cannot_bypass_bounded_slots():
    inbox = TalkInbox(2, 1)
    ticket = inbox.submit(7, (FRAME,))
    inbox.cancel(TalkTicket(1, ticket.serial))
    assert not inbox.should_cancel(ticket)
    inbox.cancel(ticket)
    with pytest.raises(ValueError, match="slot"):
        inbox.submit(7, (FRAME,))
    assert inbox.claim() is None
    assert inbox.take(ticket).status == "cancelled"
    new = inbox.submit(7, (FRAME,))
    assert new != ticket
    inbox.cancel(ticket)
    assert not inbox.should_cancel(new)
    inbox.close()
    assert inbox.take(new).status == "cancelled" and inbox.claim() is None


def configured():
    owner, job, now, output, video = session()
    negotiate(owner, output)
    inbox = MediaLeaseInbox(1, SPEC.access_id, targets=(SPEC.device,))
    lease = inbox.submit(camera(0), MediaBootstrap(SPEC, video))
    channels = []
    pump = MediaLeasePump(inbox, channels, clock=lambda: now[0])
    pump.poll(lambda spec: job)
    channel, = channels
    channel.udp = owner
    return owner, now, output, inbox, lease, pump


def test_live_lease_bridge_transmits_on_existing_owner_and_reports_receipts_not_playback():
    owner, now, output, inbox, _, pump = configured()
    ticket = inbox.submit_talk(camera(0), (FRAME,))
    pump.poll(lambda _: pytest.fail("talk cannot prepare another media route"))
    for _ in range(4):
        owner.step()
        owner.receive(ack(output[-1][0]), owner.peer)
        now[0] += .021
    pump.poll(lambda _: pytest.fail("no new route"))
    result = inbox.talk.take(ticket)
    assert result.status == "transport_received" and result.acknowledged_frames == 1
    assert result.stop_acknowledged and owner.ready and owner.result is None


def test_queued_utterance_cannot_follow_a_cancelled_lease_to_a_replacement():
    _, _, output, inbox, lease, pump = configured()
    ticket = inbox.submit_talk(camera(0), (FRAME,))
    inbox.cancel(lease)
    before = len(output)
    pump.poll(lambda _: pytest.fail("no replacement route"))
    assert inbox.talk.take(ticket).status == "unavailable"
    # request_stop may queue AV CLOSE, but the audio handoff itself emitted nothing.
    assert len(output) == before


def test_cancel_active_utterance_drains_off_and_retains_video():
    owner, _, output, inbox, _, pump = configured()
    ticket = inbox.submit_talk(camera(0), (FRAME,) * 100)
    pump.poll(lambda _: pytest.fail("no new route"))
    for _ in range(2):  # header + microphone ON, no audio emitted.
        owner.step()
        owner.receive(ack(output[-1][0]), owner.peer)
    inbox.talk.cancel(ticket)
    pump.poll(lambda _: pytest.fail("no new route"))
    owner.step()
    owner.receive(ack(output[-1][0]), owner.peer)
    pump.poll(lambda _: pytest.fail("no new route"))
    result = inbox.talk.take(ticket)
    assert result.status == "cancelled" and result.stop_acknowledged
    assert result.acknowledged_frames == 0 and owner.ready


def test_account_shutdown_never_fabricates_microphone_stop_receipt():
    owner, _, output, inbox, _, pump = configured()
    ticket = inbox.submit_talk(camera(0), (FRAME,))
    pump.poll(lambda _: pytest.fail("no new route"))
    for _ in range(2):
        owner.step()
        owner.receive(ack(output[-1][0]), owner.peer)
    # Synthetic abrupt retirement: no microphone OFF ACK is available.
    owner._owner.close()
    pump._talk.finish()
    result = inbox.talk.take(ticket)
    assert result.status == "unavailable" and not result.stop_acknowledged


def test_actual_account_worker_services_audio_on_existing_channel(monkeypatch):
    from types import SimpleNamespace

    from tests.test_media_lease_worker import Tcp, execute, setup

    sock, inbox, transports, worker = setup(monkeypatch)
    inbox.submit(camera(0), bootstrap(0))
    submissions, calls = [], []
    receive = sock.recvfrom

    def begin(tcp, frames):
        calls.append(tcp)
        assert frames == (FRAME,)
        return SimpleNamespace(done=True, closed=False, acknowledged_frames=1,
                               microphone_stopped=True, cancel=lambda: None)

    def poll(size):
        if transports and transports[0].ready and not submissions:
            submissions.append(inbox.submit_talk(camera(0), (FRAME,)))
        return receive(size)

    monkeypatch.setattr(Tcp, "begin_talk", begin, raising=False)
    sock.recvfrom = poll
    summary = execute(sock, inbox, worker)
    assert summary.reason == "stopped"
    assert len(submissions) == len(calls) == len(transports) == 1
    assert calls[0] is transports[0]  # No second media opener or camera connection.
    assert inbox.talk.take(submissions[0]).status == "transport_received"


def test_driver_view_cannot_retarget_or_use_an_old_account_generation():
    from backend.app.drivers.yoosee.p2p.account_read_lifecycle import AccountReadLifecycle
    from backend.app.drivers.yoosee.shared_device_controls import SharedDeviceControls

    lifecycle = AccountReadLifecycle()
    view = SharedDeviceControls()
    view._camera_id, view._lifecycle = camera(0), lifecycle
    with pytest.raises(ValueError, match="not accepting"):
        view.submit_talk((FRAME,))
    inbox = MediaLeaseInbox(2, 123, targets=TARGETS)
    inbox.submit(camera(0), bootstrap(0))
    inbox.claim()
    lifecycle._lease_inbox, lifecycle._state, lifecycle._generation = inbox, "active", 2
    ticket = view.submit_talk((FRAME,))
    view.cancel_talk(TalkTicket(1, ticket.serial))
    assert not inbox.talk.should_cancel(ticket)
    assert view.take_talk(TalkTicket(1, ticket.serial)) is None
    view.cancel_talk(ticket)
    assert view.take_talk(ticket).status == "cancelled"
