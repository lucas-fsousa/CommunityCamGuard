from dataclasses import replace

import pytest

from backend.app.drivers.yoosee.p2p.account_media_channel import MediaBootstrap, MediaChannelSummary
from backend.app.drivers.yoosee.p2p.media_lease_inbox import MediaLeaseInbox, MediaLeaseTicket
from backend.app.drivers.yoosee.p2p.media_route_job import MediaRouteResult, MediaRouteSpec
from backend.app.drivers.yoosee.p2p.video_delivery import VideoDelivery
from tests.test_broker_reads import DEVICE

TARGETS = tuple(replace(DEVICE, device_id=100 + index) for index in range(3))


def camera(index):
    return f"cam_{index:024x}"


def bootstrap(index):
    return MediaBootstrap(MediaRouteSpec(123, replace(DEVICE, device_id=100 + index)), VideoDelivery())


def test_unread_cancelled_result_retains_capacity_until_collected():
    inbox = MediaLeaseInbox(1, 123, targets=TARGETS[:1], capacity=1)
    first = bootstrap(0)
    ticket = inbox.submit(camera(0), first)
    assert inbox.cancel(ticket) and first.video.closed
    assert inbox.claim() is None
    with pytest.raises(ValueError):
        inbox.submit(camera(1), bootstrap(1))
    assert inbox.take(ticket).status == "cancelled"
    replacement = inbox.submit(camera(0), bootstrap(0))
    assert replacement != ticket
    assert not inbox.cancel(ticket) and inbox.take(ticket) is None
    assert not inbox.should_cancel(replacement)


def test_claimed_cancellation_waits_for_cleanup_and_stale_generation_cannot_cancel():
    inbox = MediaLeaseInbox(2, 123, targets=TARGETS)
    spec = bootstrap(0)
    ticket = inbox.submit(camera(0), spec)
    assert inbox.claim() == (ticket, spec)
    assert not inbox.cancel(MediaLeaseTicket(1, ticket.serial))
    assert inbox.cancel(ticket) and inbox.take(ticket) is None
    assert not spec.video.closed  # Worker, not the caller, must retire this transport.
    inbox.finish(ticket, MediaChannelSummary(100, MediaRouteResult(False, False, False), None))
    assert inbox.take(ticket).status == "closed"


@pytest.mark.parametrize("fault", ["camera", "device", "delivery", "account", "offline", "closed"])
def test_exact_identity_and_separate_deliveries_are_required(fault):
    inbox = MediaLeaseInbox(1, 123, targets=TARGETS)
    first, second = bootstrap(0), bootstrap(1)
    inbox.submit(camera(0), first)
    selected_camera = camera(1)
    if fault == "camera":
        selected_camera = camera(0)
    if fault == "device":
        second = replace(second, spec=first.spec)
    if fault == "delivery":
        second = replace(second, video=first.video)
    if fault == "account":
        second = replace(second, spec=replace(second.spec, access_id=999))
    if fault == "offline":
        second = replace(second, spec=replace(second.spec, device=replace(second.spec.device, status=0)))
    if fault == "closed":
        second.video.close()
    with pytest.raises(ValueError):
        inbox.submit(selected_camera, second)


def test_uncertain_cleanup_blocks_new_leases_even_after_result_is_taken():
    inbox = MediaLeaseInbox(1, 123, targets=TARGETS)
    ticket = inbox.submit(camera(0), bootstrap(0))
    inbox.claim()
    with pytest.raises(ValueError, match="identity"):
        inbox.finish(ticket, MediaChannelSummary(101, MediaRouteResult(True, True, True), None))
    assert inbox.take(ticket) is None
    inbox.finish(ticket, MediaChannelSummary(100, MediaRouteResult(True, True, False), None))
    assert inbox.take(ticket).status == "blocked"
    with pytest.raises(ValueError):
        inbox.submit(camera(1), bootstrap(1))


def test_close_cancels_queued_but_does_not_fabricate_active_cleanup():
    inbox = MediaLeaseInbox(1, 123, targets=TARGETS)
    active = inbox.submit(camera(0), bootstrap(0))
    inbox.claim()
    queued = inbox.submit(camera(1), bootstrap(1))
    inbox.close()
    assert inbox.take(queued).status == "cancelled"
    assert inbox.should_cancel(active) and inbox.take(active) is None
    assert inbox.claim() is None


@pytest.mark.parametrize("generation,access,capacity", [(0, 123, 3), (True, 123, 3),
    (1, True, 3), (1, -1, 3), (1, 123, 4), (1, 123, 0)])
def test_invalid_account_boundaries_fail_before_admission(generation, access, capacity):
    with pytest.raises(ValueError):
        MediaLeaseInbox(generation, access, targets=TARGETS[:capacity], capacity=capacity)
