"""Tests for the Yoosee driver package. The ONVIF toolboxes (ptz/device/
media) are stubbed, so detection, the control probe and PTZ routing run offline.
"""
import pytest

from backend.app.control import device, media, ptz
from backend.app.db.p2p import P2PEnrollment
from backend.app.db.registry import Camera
from backend.app.drivers.base import Capabilities, DetectContext, Unsupported
from backend.app.drivers.yoosee import YooseeDriver
from backend.app.drivers.yoosee import controls as yoosee_controls
from backend.app.drivers.yoosee.p2p import P2PWhiteLightWrite


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
    with pytest.raises(Unsupported):
        _drv().reboot(Camera(mac="aa:bb:cc:dd:ee:01"))


# --- proprietary controls stay behind the driver -----------------------------------

def test_control_catalog_requires_exact_linked_enrollment(monkeypatch):
    camera = Camera(
        mac="aa:bb:cc:dd:ee:01",
        camera_id="cam_0123456789abcdef01234567",
    )
    monkeypatch.setattr(
        yoosee_controls.p2p,
        "has_enrollment_for_camera",
        lambda camera_id: camera_id == camera.camera_id,
    )

    catalog = {item.key: item for item in _drv().control_catalog(camera)}

    assert set(catalog) == {"white_light", "orientation"}
    assert catalog["white_light"].readable is True
    assert catalog["orientation"].options == ("normal", "inverted")


def test_white_light_write_maps_semantic_control_to_yoosee_adapter(monkeypatch):
    camera = Camera(
        mac="aa:bb:cc:dd:ee:01",
        camera_id="cam_0123456789abcdef01234567",
    )
    enrollment = P2PEnrollment(
        "7000000001", 123, bytes(range(64)), None, "now", "now", camera.camera_id
    )
    observed = []
    monkeypatch.setattr(
        yoosee_controls.p2p,
        "get_enrollment_for_camera",
        lambda camera_id: enrollment if camera_id == camera.camera_id else None,
    )
    monkeypatch.setattr(
        yoosee_controls,
        "run_with_fresh_access",
        lambda selected, operation: operation(selected),
    )

    def fake_write(selected, enabled):
        observed.append((selected.device_id, enabled))
        return P2PWhiteLightWrite(selected.device_id, enabled, False, True, True, True, True)

    monkeypatch.setattr(yoosee_controls, "set_camera_white_light", fake_write)

    result = _drv().write_control(camera, "white_light", True)

    assert observed == [("7000000001", True)]
    assert result.key == "white_light"
    assert result.value is True
    assert result.previous_value is False
    assert result.verified is True
