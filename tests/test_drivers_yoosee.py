"""Tests for the Yoosee driver (backend/app/drivers/yoosee.py). The ONVIF toolboxes (ptz/device/
media) are stubbed, so detection, the control probe and PTZ routing run offline.
"""
from backend.app.control import device, media, ptz
from backend.app.db.registry import Camera
from backend.app.drivers.base import Capabilities, DetectContext
from backend.app.drivers.yoosee import YooseeDriver


def _drv():
    return YooseeDriver()


# --- matches ------------------------------------------------------------------------

def test_matches_by_vendor_string():
    d = _drv()
    for vendor in ("Yoosee", "RtspServer_0.0.0.2", "HiSilicon"):
        assert d.matches(DetectContext(vendor=vendor)) is True


def test_matches_by_onvif_port_fingerprint():
    assert _drv().matches(DetectContext(vendor="", open_ports=[554, 5000])) is True


def test_matches_false_for_unrelated():
    assert _drv().matches(DetectContext(vendor="Acme", open_ports=[80])) is False


# --- _probe_controls ----------------------------------------------------------------

def test_probe_controls_fills_ptz_model_and_paths(monkeypatch):
    monkeypatch.setattr(ptz, "supports_ptz", lambda ip, **k: True)
    monkeypatch.setattr(device, "info", lambda ip, **k: {"model": "IPC", "firmware": "1.0"})
    monkeypatch.setattr(media, "stream_paths", lambda ip, **k: ["/onvif1", "/onvif2"])
    caps = Capabilities(driver="yoosee")
    _drv()._probe_controls(Camera(mac="aa:bb:cc:dd:ee:01", last_ip="10.0.0.9"), caps)
    assert caps.ptz is True and caps.ptz_protocol == "onvif"
    assert caps.model == "IPC" and caps.firmware == "1.0"
    assert caps.stream_paths == ["/onvif1", "/onvif2"]


def test_probe_controls_without_ip_does_nothing(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("touched the network")
    monkeypatch.setattr(ptz, "supports_ptz", boom)
    caps = Capabilities(driver="yoosee")
    _drv()._probe_controls(Camera(mac="aa:bb:cc:dd:ee:01", last_ip=""), caps)
    assert caps.ptz is False


def test_probe_controls_no_ptz_when_probe_says_no(monkeypatch):
    monkeypatch.setattr(ptz, "supports_ptz", lambda ip, **k: False)
    monkeypatch.setattr(device, "info", lambda ip, **k: None)
    monkeypatch.setattr(media, "stream_paths", lambda ip, **k: [])
    caps = Capabilities(driver="yoosee")
    _drv()._probe_controls(Camera(mac="aa:bb:cc:dd:ee:01", last_ip="10.0.0.9"), caps)
    assert caps.ptz is False and caps.stream_paths == []


# --- ptz routing --------------------------------------------------------------------

def test_ptz_routes_to_the_right_helper(monkeypatch):
    calls = []
    monkeypatch.setattr(ptz, "halt", lambda cam: calls.append("halt") or True)
    monkeypatch.setattr(ptz, "start", lambda cam, d: calls.append(f"start:{d}") or True)
    monkeypatch.setattr(ptz, "move", lambda cam, d: calls.append(f"move:{d}") or True)
    d, cam = _drv(), Camera(mac="aa:bb:cc:dd:ee:01", last_ip="10.0.0.9")
    assert d.ptz(cam, None, "stop") is True
    assert d.ptz(cam, "left", "start") is True
    assert d.ptz(cam, "right", "step") is True
    assert calls == ["halt", "start:left", "move:right"]


def test_reboot_is_unsupported():
    from backend.app.drivers.base import Unsupported
    import pytest
    with pytest.raises(Unsupported):
        _drv().reboot(Camera(mac="aa:bb:cc:dd:ee:01"))
