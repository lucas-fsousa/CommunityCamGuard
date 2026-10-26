"""Explicit grants and fail-closed account reuse, with no camera connections."""

import pytest

from backend.app.drivers.yoosee import shared_read_session as module
from backend.app.drivers.yoosee.p2p.account_policy import AccountPolicy
from backend.app.drivers.yoosee.p2p.account_read_lifecycle import (
    AccountReadLifecycle,
    ReadConnection,
)
from backend.app.drivers.yoosee.p2p.ptz_authority import PtzGrant
from backend.app.drivers.yoosee.p2p.video_delivery import VideoDelivery
from tests.test_account_media_motion import MEDIA, MOTION, setup
from tests.test_broker_reads import DEVICE, NODE
from tests.test_read_preparation import ENROLLMENT
from tests.test_read_preparation import owner as owner


@pytest.mark.parametrize("silent", [False, True])
def test_lifecycle_keeps_unknown_stop_blocked_even_when_media_release_succeeded(monkeypatch, silent):
    _unused, sock, tcp = setup(monkeypatch, "silent" if silent else None)
    lifecycle = AccountReadLifecycle(policy=AccountPolicy.CONTINUOUS, clock=lambda: sock.now,
        motion_grants=(PtzGrant(123, DEVICE.device_id, frozenset({"right"})),))
    opens, tickets = [], []
    def connect(generation):
        opens.append(generation)
        return ReadConnection(NODE, sock, "192.0.2.1", 1234, DEVICE)
    original = sock.recvfrom
    def receive(size):
        if tcp.ready and not tickets:
            tickets.append(lifecycle.submit_motion(MOTION))
        return original(size)
    monkeypatch.setattr(sock, "recvfrom", receive)
    result = lifecycle.run_media_motion(connect, media=lambda _: MEDIA, delivery=VideoDelivery(),
                                        cancelled=lambda: sock.now >= 1)
    assert opens == [1] and result.media.broker_release and result.tcp.close_acknowledged
    assert result.motion.release_delivery_confirmed == (not silent)
    assert lifecycle.state == ("blocked" if silent else "idle")
    if silent:
        with pytest.raises(ValueError, match="busy or cleanup"):
            lifecycle.run_media_motion(lambda _: pytest.fail("new authentication"),
                media=lambda _: MEDIA, delivery=VideoDelivery(), cancelled=lambda: False)


def test_media_motion_requires_grants_before_authentication():
    lifecycle = AccountReadLifecycle(policy=AccountPolicy.CONTINUOUS)
    with pytest.raises(ValueError, match="grants"):
        lifecycle.run_media_motion(lambda _: pytest.fail("authentication"), media=lambda _: MEDIA,
                                  delivery=VideoDelivery(), cancelled=lambda: False)


def test_session_cannot_borrow_another_device_motion_grant():
    grant = PtzGrant(123, int(ENROLLMENT.device_id)+1, frozenset({"right"}))
    with pytest.raises(ValueError, match="exact media device"):
        module.SharedReadSession(ENROLLMENT.camera_id, ENROLLMENT.device_id, motion_grants=(grant,))


def test_changed_access_scope_is_rejected_before_new_connection(monkeypatch, owner):
    monkeypatch.setattr(module.p2p, "read_registered_enrollment", lambda _: ENROLLMENT)
    monkeypatch.setattr(module, "open_read_connection", lambda *a, **k: pytest.fail("authentication"))
    grant = PtzGrant(ENROLLMENT.access_id+1, int(ENROLLMENT.device_id), frozenset({"right"}))
    session = module.SharedReadSession(ENROLLMENT.camera_id, ENROLLMENT.device_id, motion_grants=(grant,))
    result = session.run(video_delivery=VideoDelivery(), cancelled=lambda: False)
    assert result.reason == "motion_scope_changed"


def test_granted_session_cannot_move_without_explicit_media_delivery():
    grant = PtzGrant(ENROLLMENT.access_id, int(ENROLLMENT.device_id), frozenset({"right"}))
    session = module.SharedReadSession(ENROLLMENT.camera_id, ENROLLMENT.device_id, motion_grants=(grant,))
    with pytest.raises(ValueError, match="media delivery"):
        session.run(cancelled=lambda: False)
    with pytest.raises(ValueError, match="active media motion"):
        session.submit_motion("right")
