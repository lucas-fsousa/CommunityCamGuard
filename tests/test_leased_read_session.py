from dataclasses import replace

import pytest

from backend.app.drivers.yoosee.leased_read_session import LeasedReadSession
from backend.app.drivers.yoosee.p2p.account_media_channel import MediaChannelSummary
from backend.app.drivers.yoosee.p2p.contracts import P2PProbeError
from backend.app.drivers.yoosee.p2p.media_lease_inbox import MediaLeaseInbox
from backend.app.drivers.yoosee.p2p.media_route_job import MediaRouteResult
from backend.app.drivers.yoosee.p2p.video_delivery import VideoDelivery
from tests.test_av_route import ENROLLMENT
from tests.test_media_lease_inbox import TARGETS, bootstrap, camera


class Owner:
    state = "active"
    active = True

    def owns_media_leases(self, inbox):
        return self.active

    def stop(self):
        pytest.fail("a source must not stop the account")

    def submit(self, device_id, path):
        return device_id, path


def setup():
    entry = replace(ENROLLMENT, camera_id=camera(0), device_id="100", access_id=123)
    inbox = MediaLeaseInbox(1, 123, targets=TARGETS)
    owner, now = Owner(), [0.0]
    source = LeasedReadSession(entry, TARGETS[0], owner, inbox,
        current_enrollment=lambda: entry, clock=lambda: now[0], wait=lambda _: None)
    return source, owner, inbox, now


def test_source_cancellation_detaches_lease_not_account_and_controls_stay_device_bound():
    source, owner, inbox, now = setup()
    claimed = []
    def wait(seconds):
        now[0] += seconds
        if not claimed:
            claimed.append(inbox.claim()[0])
            assert source.matches_enrollment(source._entry)
            assert source.submit("ProConst._productInfo") == (100, "ProConst._productInfo")
        if inbox.should_cancel(claimed[0]):
            inbox.finish(claimed[0], MediaChannelSummary(100, MediaRouteResult(False, False, False), None))
    source._wait = wait
    delivery = VideoDelivery()
    result = source.run(video_delivery=delivery, cancelled=lambda: now[0] >= 0.02)
    assert result.reason == "stopped" and owner.active and delivery.closed
    diagnostic = source.diagnostics()
    assert diagnostic["session_lease_detached"] is True
    assert diagnostic["session_broker_socket_closed"] is False
    assert not source.matches_enrollment(source._entry)
    assert inbox.take(claimed[0]) is None  # Collected exactly once by this source.


def test_cancel_callback_exception_waits_for_retirement_before_escaping():
    source, _, inbox, now = setup()
    claimed = []
    def wait(seconds):
        now[0] += seconds
        if not claimed:
            claimed.append(inbox.claim()[0])
        if inbox.should_cancel(claimed[0]):
            inbox.finish(claimed[0], MediaChannelSummary(100, MediaRouteResult(False, False, False), None))
    def cancelled():
        if now[0] >= 0.02:
            raise KeyboardInterrupt
        return False
    source._wait = wait
    delivery = VideoDelivery()
    with pytest.raises(KeyboardInterrupt):
        source.run(video_delivery=delivery, cancelled=cancelled)
    assert source.diagnostics()["session_lease_detached"] is True and delivery.closed


def test_no_cleanup_receipt_means_no_success_and_no_slot_reuse():
    source, owner, inbox, now = setup()
    claimed = []
    def wait(seconds):
        now[0] += 1
        if not claimed:
            claimed.append(inbox.claim()[0])
    source._wait = wait
    delivery = VideoDelivery()
    with pytest.raises(P2PProbeError, match="deadline"):
        source.run(video_delivery=delivery, cancelled=lambda: now[0] >= 1)
    assert now[0] == 6 and owner.active and delivery.closed
    assert not source.diagnostics()["session_lease_detached"]
    with pytest.raises(ValueError, match="duplicate"):
        inbox.submit(camera(0), bootstrap(0))


def test_changed_enrollment_never_submits_or_authenticates():
    source, owner, inbox, _ = setup()
    source._current = lambda: replace(source._entry, updated_at="changed")
    delivery = VideoDelivery()
    assert source.run(video_delivery=delivery, cancelled=lambda: False).reason == "enrollment_changed"
    assert inbox.claim() is None and owner.active and delivery.closed


def test_inactive_account_never_creates_its_own_login():
    source, owner, inbox, _ = setup()
    owner.active = False
    assert source.run(video_delivery=VideoDelivery(), cancelled=lambda: False).reason == "busy"
    assert inbox.claim() is None


def test_account_closing_between_readiness_check_and_submit_is_a_refused_admission():
    source, owner, inbox, _ = setup()
    inbox.close()  # Owner's prior readiness check cannot make submission atomic.
    video = VideoDelivery()
    result = source.run(video_delivery=video, cancelled=lambda: False)
    assert result.reason == 'busy' and video.closed and owner.active
    assert source.diagnostics()['session_lease_detached'] is True
    assert inbox.claim() is None
