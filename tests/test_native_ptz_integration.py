"""Driver selection and dispatch without live movement or network."""
from dataclasses import replace
from types import SimpleNamespace

import pytest

from backend.app.drivers.contracts import ControlNotReady, ControlOperationError
from backend.app.drivers.yoosee import native_ptz, native_ptz_policy
from backend.app.drivers.yoosee.driver import YooseeDriver
from backend.app.drivers.yoosee.p2p.contracts import P2PProbeError
from backend.app.drivers.yoosee.p2p.ptz_motion import MotionResult
from tests.test_ptz_prepare import IDENTITY

CAMERA = SimpleNamespace(camera_id="cam_" + "a" * 24)
PROFILE = native_ptz_policy.PtzProfile(IDENTITY, frozenset({"right"}))


@pytest.fixture
def prepared(monkeypatch):
    state = SimpleNamespace(prepared=0, runs=0, fallback=0, outcome=MotionResult(True, False, 1, True, ()))
    class Motion:
        cancelled = False
        def __init__(self, duration):
            assert duration == 0.2
        def stop(self):
            self.cancelled = True
        def run(self, route):
            state.runs += 1
            if self.cancelled:
                return MotionResult(False, True, 0, False, ())
            return state.outcome
    monkeypatch.setattr(native_ptz, "PtzMotion", Motion)
    monkeypatch.setattr(native_ptz.p2p, "get_enrollment_for_camera", lambda _: SimpleNamespace(
        device_id=IDENTITY.device_id, access_id=1, access_token=b"test"))
    monkeypatch.setattr(native_ptz, "_routes", SimpleNamespace(acquire=lambda key, prepare: prepare()))
    def prepare(*args, **kwargs):
        state.prepared += 1
        assert kwargs["camera_id"] == CAMERA.camera_id
        assert kwargs["direction"] == "right"
        assert kwargs["budget"] == 12
        return SimpleNamespace(reused=False)
    monkeypatch.setattr(native_ptz, "prepare_ptz_route", prepare)
    def fallback():
        state.fallback += 1
        return True
    state.fallback_fn = fallback
    yield state
    assert not native_ptz._active


def test_success_does_not_fallback(prepared):
    assert native_ptz.step(CAMERA, "right", PROFILE, prepared.fallback_fn)
    assert (prepared.runs, prepared.fallback) == (1, 0)


@pytest.mark.parametrize("outcome", [MotionResult(True, False, 3, False, ()),
    MotionResult(True, False, 1, True, ("OSError",))])
def test_ambiguous_movement_never_falls_back(prepared, outcome):
    prepared.outcome = outcome
    with pytest.raises(ControlOperationError):
        native_ptz.step(CAMERA, "right", PROFILE, prepared.fallback_fn)
    assert prepared.fallback == 0


@pytest.mark.parametrize("cancel", [True, False])
def test_preparation_failure_only_falls_back_if_not_cancelled(prepared, monkeypatch, cancel):
    def fail(*args, **kwargs):
        if cancel:
            native_ptz.stop(CAMERA.camera_id)
        raise P2PProbeError("test")
    monkeypatch.setattr(native_ptz, "prepare_ptz_route", fail)
    assert native_ptz.step(CAMERA, "right", PROFILE, prepared.fallback_fn) is (not cancel)
    assert prepared.fallback == int(not cancel)
    assert prepared.runs == 0


def test_stop_during_preparation_cancels_and_no_duplicate_session(prepared, monkeypatch):
    def prepare(*args, **kwargs):
        with pytest.raises(ControlNotReady):
            native_ptz.step(CAMERA, "right", PROFILE, prepared.fallback_fn)
        native_ptz.stop(CAMERA.camera_id)
        return SimpleNamespace(reused=False)
    monkeypatch.setattr(native_ptz, "prepare_ptz_route", prepare)
    assert not native_ptz.step(CAMERA, "right", PROFILE, prepared.fallback_fn)
    assert prepared.fallback == 0


def test_wrong_enrollment_never_prepares_or_falls_back(prepared, monkeypatch):
    monkeypatch.setattr(native_ptz.p2p, "get_enrollment_for_camera", lambda _: None)
    with pytest.raises(ControlNotReady):
        native_ptz.step(CAMERA, "right", PROFILE, prepared.fallback_fn)
    assert prepared.prepared == prepared.fallback == 0


def test_driver_uses_only_reviewed_directions_and_rejects_old_hold_client(monkeypatch):
    monkeypatch.setattr(native_ptz_policy, "selected", lambda _: PROFILE)
    calls = []
    monkeypatch.setattr(native_ptz, "step", lambda *args: calls.append("native") or True)
    from backend.app.control import ptz
    monkeypatch.setattr(ptz, "move", lambda *args: calls.append("standard") or True)
    driver = YooseeDriver()
    assert driver.ptz_interaction(CAMERA) == "step"
    assert driver.ptz(CAMERA, "right", "step")
    assert driver.ptz(CAMERA, "left", "step")
    with pytest.raises(ValueError):
        driver.ptz(CAMERA, "right", "start")
    assert calls == ["native", "standard"]


def test_policy_persists_exact_unit_and_direction_only():
    assert native_ptz_policy.selected(CAMERA.camera_id) is None
    native_ptz_policy.activate(CAMERA.camera_id, IDENTITY, frozenset({"right"}))
    assert native_ptz_policy.selected(CAMERA.camera_id) == PROFILE
    assert native_ptz_policy.selected("cam_" + "b" * 24) is None
    assert native_ptz_policy.selected(CAMERA.camera_id).identity != replace(IDENTITY, firmware="changed")
