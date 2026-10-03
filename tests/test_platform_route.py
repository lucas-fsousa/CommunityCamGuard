"""Platform-only route ownership: fake sockets, no AV traffic or decoder."""

from dataclasses import replace

import pytest

from backend.app.drivers.yoosee.p2p import av_route
from backend.app.drivers.yoosee.p2p.contracts import P2PProbeError
from tests.test_av_route import CHANNEL, ENROLLMENT
from tests.test_av_route import env as env


def run(**kwargs):
    return av_route.probe_platform_route(
        ENROLLMENT, camera_id="cam_test", device_id="123", **kwargs,
    )


@pytest.fixture
def no_media():
    def forbidden(*args, **kwargs):
        pytest.fail("platform-only path must not invoke AV reception")
    # env also patches this function; each test installs this guard after env.
    return forbidden


@pytest.mark.parametrize("platform", [None, 2])
def test_platform_only_stops_at_meter_and_releases(env, monkeypatch, no_media, platform):
    monkeypatch.setattr(av_route, "probe_av_socket", no_media)
    monkeypatch.setattr(av_route, "open_media_channel",
                        lambda *a, **k: replace(CHANNEL, device_platform_version=platform))
    result = run()
    assert result == av_route.PlatformRouteResult(platform, True, True)
    attempt = env[1][1]
    assert env == ["bind", ("call", attempt), ("release", attempt.link_id, 19), "socket_close"]


@pytest.mark.parametrize("fault", ["meter", "release", "ambiguous"])
def test_failure_still_closes_without_claiming_platform(env, monkeypatch, no_media, fault):
    monkeypatch.setattr(av_route, "probe_av_socket", no_media)
    if fault == "meter":
        monkeypatch.setattr(av_route, "open_media_channel",
                            lambda *a, **k: replace(CHANNEL, meter_roundtrip_confirmed=False))
    elif fault == "release":
        monkeypatch.setattr(av_route, "close_device_route", lambda *a, **k: False)
    else:
        def fail(*args, **kwargs):
            raise OSError("ambiguous request")
        monkeypatch.setattr(av_route, "call_device", fail)
    with pytest.raises(P2PProbeError):
        run()
    assert env[-1] == "socket_close"
    if fault != "release":
        assert env[-2][0] == "release"


def test_cancelled_before_socket(env):
    with pytest.raises(P2PProbeError, match="cancelled"):
        run(cancelled=lambda: True)
    assert not env
