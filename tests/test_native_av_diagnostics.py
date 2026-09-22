from types import SimpleNamespace

import pytest

from backend.app.diagnostics import yoosee_av as diagnostics
from backend.app.services import camera_controls
from tests.test_av_route import ENROLLMENT


@pytest.fixture
def env(monkeypatch):
    monkeypatch.setattr(diagnostics.registry, "get_camera_by_id",
                        lambda camera_id: SimpleNamespace(camera_id=camera_id))
    monkeypatch.setattr(diagnostics.p2p, "get_enrollment_for_camera", lambda camera_id: ENROLLMENT)
    calls = []
    def probe(enrollment, **kwargs):
        calls.append(kwargs)
        with pytest.raises(camera_controls.ControlBusy):
            camera_controls.read_control("cam_test", "orientation")
        return "result"
    monkeypatch.setattr(diagnostics, "probe_av_route", probe)
    return calls


def run(**kwargs):
    return diagnostics.run_reviewed_native_av(camera_id="cam_test", reviewed_camera_id="cam_test",
                                              reviewed_device_id="123", **kwargs)


def test_shared_lock_and_fixed_duration(env):
    assert run() == "result"
    assert env[0]["duration"] == 3.0 and env[0]["device_id"] == "123"
    with camera_controls._exclusive("cam_test"):
        pass


def test_busy_camera_never_opens_a_route(env):
    with camera_controls._exclusive("cam_test"):
        with pytest.raises(camera_controls.ControlBusy):
            run()
    assert not env


def test_non_reviewed_target_never_opens_a_route(env):
    with pytest.raises(ValueError, match="reviewed"):
        diagnostics.run_reviewed_native_av(camera_id="other", reviewed_camera_id="cam_test",
                                           reviewed_device_id="123")
    assert not env


def test_mismatched_enrollment_never_opens_a_route(env):
    with pytest.raises(ValueError, match="enrollment"):
        diagnostics.run_reviewed_native_av(camera_id="cam_test", reviewed_camera_id="cam_test",
                                           reviewed_device_id="999")
    assert not env


def test_failure_releases_application_lock(env, monkeypatch):
    def fail(*args, **kwargs):
        raise RuntimeError("test failure")
    monkeypatch.setattr(diagnostics, "probe_av_route", fail)
    with pytest.raises(RuntimeError):
        run()
    with camera_controls._exclusive("cam_test"):
        pass


@pytest.mark.parametrize("fault", [None, "route", "decoder", "tools"])
def test_decode_opt_in_orders_route_before_decoder_and_always_clears(env, monkeypatch, fault):
    events, retained = [], []

    def tools():
        events.append("tools")
        if fault == "tools":
            raise RuntimeError("tools missing")

    def route(*args, **kwargs):
        events.append("route")
        retained.append(kwargs["sample"])
        kwargs["sample"].data.extend(b"private")
        if fault == "route":
            raise RuntimeError("route failure")
        return "route closed"

    def decode(sample, **kwargs):
        assert events == ["tools", "route"]
        assert sample.data == b"private"
        with pytest.raises(camera_controls.ControlBusy):
            camera_controls.read_control("cam_test", "orientation")
        events.append("decode")
        if fault == "decoder":
            raise RuntimeError("decode failure")
        return "decoded"

    monkeypatch.setattr(diagnostics, "require_decoder_tools", tools)
    monkeypatch.setattr(diagnostics, "probe_av_route", route)
    monkeypatch.setattr(diagnostics, "decode_sample", decode)
    if fault:
        with pytest.raises(RuntimeError):
            run(decode_video=True)
    else:
        result = run(decode_video=True)
        assert result.route == "route closed" and result.video == "decoded"
    assert all(value.closed and not value.data for value in retained)
    assert events == (["tools"] if fault == "tools" else ["tools", "route"] if fault == "route"
                      else ["tools", "route", "decode"])
    with camera_controls._exclusive("cam_test"):
        pass
