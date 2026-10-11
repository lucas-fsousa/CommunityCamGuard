"""Native diagnostics must not race in-process shared-account preparation."""

import threading
from types import SimpleNamespace

import pytest

from backend.app.diagnostics import yoosee_av as diagnostics
from backend.app.drivers.yoosee.p2p import control_ownership
from backend.app.services import camera_controls
from tests.test_av_route import ENROLLMENT
from tests.test_native_av_diagnostics import run


@pytest.fixture
def env(monkeypatch):
    monkeypatch.setattr(diagnostics.registry, "get_camera_by_id", lambda _: SimpleNamespace())
    monkeypatch.setattr(diagnostics.p2p, "get_enrollment_for_camera", lambda _: ENROLLMENT)
    calls = []
    monkeypatch.setattr(diagnostics, "probe_av_route", lambda *args, **kwargs: calls.append(True))
    return calls


def available_from_other_thread():
    result = []

    def check():
        acquired = control_ownership._channel.acquire(blocking=False)
        result.append(acquired)
        if acquired:
            control_ownership._channel.release()

    thread = threading.Thread(target=check)
    thread.start()
    thread.join(timeout=2)
    assert not thread.is_alive()
    return result == [True]


@pytest.mark.parametrize("mode", ["udp", "platform", "tcp"])
@pytest.mark.parametrize("failure", [False, True])
def test_broker_owned_through_probe_and_released_on_failure(env, monkeypatch, mode, failure):
    def probe(*args, **kwargs):
        assert not available_from_other_thread()
        if failure:
            raise RuntimeError("probe failure")
        return "complete"

    monkeypatch.setattr(diagnostics, {"platform": "probe_platform_route", "udp": "probe_av_route",
                                     "tcp": "probe_tcp_route"}[mode], probe)
    kwargs = dict(platform_only=mode == "platform", tcp_relay=mode == "tcp")
    if failure:
        with pytest.raises(RuntimeError):
            run(**kwargs)
    else:
        assert run(**kwargs) == "complete"
    assert available_from_other_thread()


def test_busy_broker_opens_no_route_and_releases_camera(env, monkeypatch):
    monkeypatch.setattr(control_ownership, "WAIT_SECONDS", 0.01)
    ready, release = threading.Event(), threading.Event()

    def occupy():
        with control_ownership.own_control_channel():
            ready.set()
            release.wait(3)

    thread = threading.Thread(target=occupy)
    thread.start()
    try:
        assert ready.wait(2)
        with pytest.raises(control_ownership.ControlChannelBusy):
            run()
        assert not env
        with camera_controls._exclusive("cam_test"):
            pass
    finally:
        release.set()
        thread.join(timeout=2)
    assert not thread.is_alive() and available_from_other_thread()
