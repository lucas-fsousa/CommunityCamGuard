"""Control ownership spans reconnect gaps, without a broker/socket/camera call."""

from types import SimpleNamespace

import pytest

from backend.app.drivers.contracts import ControlNotReady
from backend.app.drivers.yoosee import native_ptz, shared_control_reads
from backend.app.drivers.yoosee.native_ptz_policy import PtzProfile
from backend.app.drivers.yoosee.native_recovery import wrap
from backend.app.drivers.yoosee.native_video_source import NativeVideoResult, NativeVideoSource
from backend.app.drivers.yoosee.shared_native_motion import step
from backend.app.drivers.yoosee.shared_read_session import SharedReadResult
from backend.app.media import recovering_source
from tests.test_read_preparation import ENROLLMENT


@pytest.fixture
def owners(monkeypatch):
    monkeypatch.setattr(shared_control_reads,"_owners",{})
    return shared_control_reads._owners


def test_same_camera_control_and_background_warmup_do_not_login_during_gap(owners, monkeypatch):
    now = [0.0]
    first, second = NativeVideoSource("test","123"), NativeVideoSource("test","123")
    def first_run(**kwargs): return NativeVideoResult("stopped", SharedReadResult("stopped"))
    monkeypatch.setattr(first,"run",first_run)
    monkeypatch.setattr(second,"run",lambda **kw: NativeVideoResult("protocol",SharedReadResult("protocol")))
    monkeypatch.setattr(native_ptz.p2p,"get_enrollment_for_camera",lambda _: ENROLLMENT)
    monkeypatch.setattr(native_ptz,"run_with_fresh_access",lambda *a,**k: pytest.fail("legacy login"))
    camera = SimpleNamespace(camera_id=ENROLLMENT.camera_id)
    profile = PtzProfile(None,frozenset({"right"}))
    def sleep(duration):
        assert shared_control_reads.has_session(ENROLLMENT.camera_id)
        with pytest.raises(ControlNotReady,match="recovering"):
            shared_control_reads.session_for(ENROLLMENT)
        with pytest.raises(ControlNotReady):
            shared_control_reads.require_legacy_available(ENROLLMENT.camera_id)
        assert native_ptz.warm(camera,profile) is False
        with pytest.raises(ControlNotReady):
            step(camera,"right",profile,SimpleNamespace(cancelled=False))
        assert native_ptz.stop(ENROLLMENT.camera_id) is True
        now[0] += duration
    monkeypatch.setattr(recovering_source.time,"sleep",sleep)
    source = wrap(first,camera_id=ENROLLMENT.camera_id,factory=lambda: second,valid=lambda: True)
    source._clock = lambda: now[0]
    assert source.run(cancelled=lambda: False).reason == "protocol"
    assert not owners and not shared_control_reads.has_session(ENROLLMENT.camera_id)


def test_unknown_run_error_retains_control_reservation(owners, monkeypatch):
    first = NativeVideoSource("test","123")
    def fail(**kwargs): raise RuntimeError("uncertain cleanup")
    monkeypatch.setattr(first,"run",fail)
    source = wrap(first,camera_id=ENROLLMENT.camera_id,
                  factory=lambda: pytest.fail("replacement"),valid=lambda: True)
    with pytest.raises(RuntimeError): source.run(cancelled=lambda: False)
    assert ENROLLMENT.camera_id in owners
    with pytest.raises(ControlNotReady): shared_control_reads.session_for(ENROLLMENT)
    with pytest.raises(ControlNotReady):
        with shared_control_reads.publish("another_camera",first._session):
            pytest.fail("another owner")
