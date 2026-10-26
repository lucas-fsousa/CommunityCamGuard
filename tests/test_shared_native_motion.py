"""Existing dashboard dispatcher, synthetic native account; no physical movement."""

from types import SimpleNamespace

import pytest

from backend.app.drivers.contracts import ControlNotReady, ControlOperationError
from backend.app.drivers.yoosee import native_ptz, shared_control_reads
from backend.app.drivers.yoosee.driver import YooseeDriver
from backend.app.drivers.yoosee.native_ptz_policy import PtzProfile
from backend.app.drivers.yoosee.p2p.ptz_inbox import MotionTicket
from backend.app.drivers.yoosee.p2p.ptz_scheduler import ScheduledPtzResult
from tests.test_read_preparation import ENROLLMENT

CAMERA = SimpleNamespace(camera_id=ENROLLMENT.camera_id)
PROFILE = PtzProfile(None, frozenset({"right"}))


class Session:
    def __init__(self):
        self.ready = self.granted = True
        self.commands, self.cancelled, self.abandoned = [], [], []
        self.result = ScheduledPtzResult("released", True, 1, True, False)
        self.on_submit = lambda: None
    def matches_enrollment(self, entry): return self.ready and entry == ENROLLMENT
    def submit_motion(self, direction):
        if not self.granted: raise ValueError("no reviewed grant")
        self.commands.append(direction)
        self.on_submit()
        return MotionTicket(1, 0)
    def take_motion(self, ticket): return self.result
    def cancel_motion(self, ticket): self.cancelled.append(ticket); return True
    def abandon_motion(self, ticket): self.abandoned.append(ticket)


@pytest.fixture
def setup(monkeypatch):
    session = Session()
    monkeypatch.setattr(native_ptz.p2p, "get_enrollment_for_camera", lambda _: ENROLLMENT)
    monkeypatch.setattr(native_ptz, "run_with_fresh_access", lambda *a, **k: pytest.fail("another login"))
    monkeypatch.setattr(native_ptz, "prepare_ptz_route", lambda *a, **k: pytest.fail("legacy preparation"))
    with shared_control_reads.publish(ENROLLMENT.camera_id, session):
        yield session
    assert not native_ptz._active


def fallback(): pytest.fail("shared motion must not fall back to ONVIF or another route")


def test_dashboard_gesture_reuses_shared_account_and_keeps_stop_hook(setup):
    assert native_ptz.step(CAMERA, "right", PROFILE, fallback)
    assert setup.commands == ["right"] and setup.abandoned == [MotionTicket(1, 0)]


@pytest.mark.parametrize("fault", ["grant", "credentials", "direction", "unknown_stop", "send"])
def test_shared_failure_never_opens_a_legacy_route(setup, fault):
    direction = "right"
    if fault == "grant": setup.granted = False
    if fault == "credentials": setup.ready = False
    if fault == "direction": direction = "left"
    if fault == "unknown_stop": setup.result = ScheduledPtzResult("release_unconfirmed", True, 3, False, False)
    if fault == "send": setup.result = ScheduledPtzResult("released", True, 1, True, True)
    with pytest.raises((ControlNotReady, ControlOperationError)):
        native_ptz.step(CAMERA, direction, PROFILE, fallback)
    assert len(setup.commands) <= 1


def test_stop_arriving_before_ticket_is_assigned_is_forwarded_without_repeating(setup):
    setup.on_submit = lambda: native_ptz.stop(CAMERA.camera_id)
    setup.result = ScheduledPtzResult("cancelled", False, 0, False, False)
    assert native_ptz.step(CAMERA, "right", PROFILE, fallback) is False
    assert setup.commands == ["right"] and setup.cancelled == [MotionTicket(1, 0)]


def test_timeout_abandons_to_socket_owner_without_releasing_pending_stop_ownership(setup, monkeypatch):
    from backend.app.drivers.yoosee import shared_native_motion
    now = iter([0.0, 5.0])
    monkeypatch.setattr(shared_native_motion, "time", SimpleNamespace(
        monotonic=lambda: next(now), sleep=lambda _: pytest.fail("timeout must not wait")))
    with pytest.raises(ControlOperationError, match="timed out"):
        native_ptz.step(CAMERA, "right", PROFILE, fallback)
    assert setup.commands == ["right"] and setup.abandoned == [MotionTicket(1, 0)]


@pytest.mark.parametrize("profile", [None, PROFILE])
def test_driver_cannot_bypass_shared_scope_through_onvif_fallback(setup, monkeypatch, profile):
    from backend.app.control import ptz
    from backend.app.drivers.yoosee import native_ptz_policy
    monkeypatch.setattr(native_ptz_policy, "selected", lambda _: profile)
    monkeypatch.setattr(ptz, "move", lambda *a, **k: pytest.fail("parallel ONVIF move"))
    with pytest.raises(ControlNotReady):
        YooseeDriver().ptz(CAMERA, "left")
    assert not setup.commands


def test_stop_keeps_working_when_profile_is_no_longer_available(setup, monkeypatch):
    from backend.app.drivers.yoosee import native_ptz_policy
    calls = []
    native_ptz._active[CAMERA.camera_id] = SimpleNamespace(stop=lambda: calls.append("stop"))
    monkeypatch.setattr(native_ptz_policy, "selected", lambda _: pytest.fail("stop needs no profile lookup"))
    try:
        assert YooseeDriver().ptz(CAMERA, None, action="stop")
        assert calls == ["stop"]
    finally:
        native_ptz._active.pop(CAMERA.camera_id)
