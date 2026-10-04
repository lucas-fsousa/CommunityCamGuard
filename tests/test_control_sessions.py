"""Background preparation is generic, sequential, read-only and backs off."""
from types import SimpleNamespace

from backend.app.drivers.base import CameraDriver
from backend.app.drivers.yoosee.driver import YooseeDriver
from backend.app.services import control_sessions


def test_driver_default_does_not_probe_or_actuate():
    driver = CameraDriver()
    driver.maintain_control_session(SimpleNamespace())
    driver.close_control_sessions()


def test_sequential_preparation_and_failure_backoff(monkeypatch):
    state = SimpleNamespace(now=0, calls=[])
    cameras = [SimpleNamespace(camera_id="one"), SimpleNamespace(camera_id="two")]
    monkeypatch.setattr(control_sessions.registry, "list_cameras", lambda: cameras)
    monkeypatch.setattr(control_sessions.time, "monotonic", lambda: state.now)
    def warm(camera):
        state.calls.append(camera.camera_id)
        if camera.camera_id == "one":
            raise OSError("do not log secrets")
    monkeypatch.setattr(control_sessions.drivers, "for_camera",
                        lambda _: SimpleNamespace(maintain_control_session=warm))
    worker = control_sessions.ControlSessions()
    worker.tick()
    worker.tick()
    assert state.calls == ["one", "two", "two"]
    assert worker._failures["one"] == (1, 30)
    state.now = 30
    worker.tick()
    assert worker._failures["one"] == (2, 90)
    cameras.pop(0)
    worker.tick()
    assert not worker._failures
    worker.stop()
    state.calls.clear()
    worker.tick()
    assert not state.calls


def test_driver_requires_per_camera_ptz_and_native_profile(monkeypatch):
    from backend.app.drivers.yoosee import native_ptz, native_ptz_policy

    calls = []
    monkeypatch.setattr(native_ptz_policy, "selected", lambda _: "profile")
    monkeypatch.setattr(native_ptz, "warm", lambda *args: calls.append(args))
    driver = YooseeDriver()
    camera = SimpleNamespace(camera_id="test", capabilities={})
    driver.maintain_control_session(camera)
    assert not calls
    camera.capabilities = {"ptz": True}
    driver.maintain_control_session(camera)
    assert calls == [(camera, "profile")]
    monkeypatch.setattr(native_ptz_policy, "selected", lambda _: None)
    driver.maintain_control_session(camera)
    assert len(calls) == 1


def test_worker_cleanup_runs_even_on_stop(monkeypatch):
    calls = []
    monkeypatch.setattr(control_sessions.drivers, "DRIVERS", (
        SimpleNamespace(close_control_sessions=lambda: calls.append("closed")),))
    worker = control_sessions.ControlSessions()
    worker.stop()
    worker._run()
    assert calls == ["closed"]
