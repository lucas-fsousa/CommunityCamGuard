"""Tests for the CameraDriver base (backend/app/drivers/base.py): detection, the shared RTSP
capability probe, and the default-unsupported controls. The RTSP session + parsing are stubbed
(they have their own tests), so this exercises the driver logic offline.
"""
import pytest

from backend.app.drivers import base
from backend.app.drivers.base import CameraDriver, DetectContext, Unsupported
from backend.app.db.registry import Camera


class FakeSession:
    """Stand-in for rtsp.RtspSession: hands back queued responses in order."""
    def __init__(self, responses):
        self._responses = list(responses)
        self.requests = []

    def request(self, method, uri, **kw):
        self.requests.append((method, kw))
        return self._responses.pop(0) if self._responses else None

    def close(self):
        pass


def _cam(ip="10.0.0.5", **kw):
    return Camera(mac="aa:bb:cc:dd:ee:05", last_ip=ip, **kw)


# --- detection + default controls ---------------------------------------------------

def test_base_driver_matches_nothing():
    assert CameraDriver().matches(DetectContext(vendor="anything", open_ports=[554])) is False


def test_default_controls_are_unsupported():
    d, cam = CameraDriver(), _cam()
    with pytest.raises(Unsupported):
        d.ptz(cam, "left")
    with pytest.raises(Unsupported):
        d.reboot(cam)


# --- probe / _probe_rtsp ------------------------------------------------------------

def test_probe_without_ip_returns_caps_but_not_reachable():
    caps = CameraDriver().probe(_cam(ip=""), open_ports=[554, 5000])
    assert caps.driver == "generic"
    assert caps.reachable is False
    assert caps.open_ports == [554, 5000]         # sorted/deduped
    assert "rtsp" in caps.ports_by_role


def test_probe_reads_sdp_tracks_when_reachable(monkeypatch):
    monkeypatch.setattr(base.rtsp, "RtspSession",
                        lambda *a, **k: FakeSession(["OPTS", "DESCR"]))
    monkeypatch.setattr(base.rtsp, "parse_status", lambda r: 200)  # OPTIONS + DESCRIBE both OK
    monkeypatch.setattr(base.rtsp, "parse_sdp", lambda r: {
        "has_video": True, "has_audio": True, "video_codec": "h265", "audio_codec": "pcma"})
    caps = CameraDriver().probe(_cam(username="admin", password="x"), open_ports=[554])
    assert caps.reachable is True
    assert caps.has_video and caps.has_audio
    assert caps.video_codec == "h265" and caps.audio_codec == "pcma"


def test_probe_retries_describe_with_auth_on_401(monkeypatch):
    sess = FakeSession(["OPTS", "D401", "D200"])
    monkeypatch.setattr(base.rtsp, "RtspSession", lambda *a, **k: sess)
    monkeypatch.setattr(base.rtsp, "parse_status",
                        lambda r: {"OPTS": 200, "D401": 401, "D200": 200}[r])
    monkeypatch.setattr(base.rtsp, "auth_header", lambda *a, **k: "Digest ...")
    monkeypatch.setattr(base.rtsp, "parse_sdp", lambda r: {
        "has_video": True, "has_audio": False, "video_codec": "h264", "audio_codec": ""})
    caps = CameraDriver().probe(_cam(username="admin", password="pw"), open_ports=[554])
    assert caps.reachable is True and caps.video_codec == "h264"
    # the authed DESCRIBE retry happened (3 requests: OPTIONS + DESCRIBE + authed DESCRIBE)
    assert len(sess.requests) == 3
    assert any("auth" in kw and kw["auth"] for _, kw in sess.requests)


def test_probe_unreachable_when_options_fails(monkeypatch):
    monkeypatch.setattr(base.rtsp, "RtspSession", lambda *a, **k: FakeSession(["OPTS"]))
    monkeypatch.setattr(base.rtsp, "parse_status", lambda r: 0)   # OPTIONS didn't answer
    caps = CameraDriver().probe(_cam(), open_ports=[554])
    assert caps.reachable is False
    assert caps.video_codec == ""


def test_probe_swallows_connection_error(monkeypatch):
    def boom(*a, **k):
        raise OSError("refused")
    monkeypatch.setattr(base.rtsp, "RtspSession", boom)
    caps = CameraDriver().probe(_cam(), open_ports=[554])   # must not raise
    assert caps.reachable is False
