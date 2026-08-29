"""Tests for the Yoosee driver package. The ONVIF toolboxes (ptz/device/
media) are stubbed, so detection, the control probe and PTZ routing run offline.
"""

import base64
import struct

import pytest

from backend.app.control import device, media, ptz
from backend.app.db.p2p import P2PEnrollment
from backend.app.db.registry import Camera
from backend.app.drivers.base import Capabilities, DetectContext, Unsupported
from backend.app.drivers.contracts import WeeklySchedule
from backend.app.drivers.yoosee import YooseeDriver
from backend.app.drivers.yoosee import controls as yoosee_controls
from backend.app.drivers.yoosee.p2p import (
    P2PAlarmVoiceCatalog,
    P2PAlarmVoiceWrite,
    P2PNightVisionWrite,
    P2PSirenPulse,
    P2PSmartProtectionScheduleState,
    P2PSmartProtectionScheduleWrite,
    P2PSmartProtectionState,
    P2PSmartProtectionWrite,
    P2PSpeakerVolumeState,
    P2PSpeakerVolumeWrite,
    P2PWhiteLightWrite,
)
from backend.app.drivers.yoosee.p2p.alarm_voice import AlarmVoiceResource


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

    assert set(catalog) == {
        "white_light",
        "orientation",
        "siren_pulse",
        "speaker_volume",
        "night_vision",
        "smart_protection",
        "smart_protection_schedule",
        "alarm_voice",
    }
    assert catalog["white_light"].readable is True
    assert catalog["orientation"].options == ("normal", "inverted")
    assert catalog["siren_pulse"].kind == "action"
    assert catalog["siren_pulse"].options == ("2", "5", "10")
    assert catalog["speaker_volume"].readable is True
    assert catalog["speaker_volume"].options == ("0", "25", "50", "75", "100")
    assert catalog["night_vision"].options == ("automatic", "daytime", "night")
    assert catalog["smart_protection"].readable is True
    assert catalog["smart_protection"].writable is True
    assert catalog["smart_protection_schedule"].kind == "weekly_schedule"
    assert catalog["smart_protection_schedule"].readable is True
    assert catalog["alarm_voice"].kind == "choice"
    assert catalog["alarm_voice"].dynamic_options is True
    assert catalog["alarm_voice"].options == ()


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


def test_siren_write_maps_only_bounded_duration_to_yoosee_adapter(monkeypatch):
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

    def fake_pulse(selected, duration_seconds):
        observed.append((selected.device_id, duration_seconds))
        return P2PSirenPulse(selected.device_id, duration_seconds, True, 0, True, 0, True)

    monkeypatch.setattr(yoosee_controls, "pulse_camera_siren", fake_pulse)

    result = _drv().write_control(camera, "siren_pulse", 5)

    assert observed == [("7000000001", 5)]
    assert result.value == 5
    assert result.changed is True
    assert result.verified is True
    assert result.application_acknowledged is True


@pytest.mark.parametrize("value", [True, 1, 3, 11, "5"])
def test_siren_write_rejects_duration_outside_advertised_choices(monkeypatch, value):
    camera = Camera(
        mac="aa:bb:cc:dd:ee:01",
        camera_id="cam_0123456789abcdef01234567",
    )
    enrollment = P2PEnrollment(
        "7000000001", 123, bytes(range(64)), None, "now", "now", camera.camera_id
    )
    monkeypatch.setattr(
        yoosee_controls.p2p,
        "get_enrollment_for_camera",
        lambda _camera_id: enrollment,
    )

    with pytest.raises(yoosee_controls.ControlOperationError, match="2, 5 or 10"):
        _drv().write_control(camera, "siren_pulse", value)


def test_speaker_volume_read_and_write_use_normalized_percent(monkeypatch):
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
        lambda _camera_id: enrollment,
    )
    monkeypatch.setattr(
        yoosee_controls,
        "run_with_fresh_access",
        lambda selected, operation: operation(selected),
    )
    monkeypatch.setattr(
        yoosee_controls,
        "read_camera_speaker_volume",
        lambda selected: P2PSpeakerVolumeState(selected.device_id, 75, 6, True, True, True, 0),
    )

    def fake_write(selected, percent):
        observed.append((selected.device_id, percent))
        return P2PSpeakerVolumeWrite(selected.device_id, percent, 75, 6, 10, True, True, 0, True)

    monkeypatch.setattr(yoosee_controls, "set_camera_speaker_volume", fake_write)

    read = _drv().read_control(camera, "speaker_volume")
    written = _drv().write_control(camera, "speaker_volume", 100)

    assert read.value == 75
    assert read.verified is True
    assert observed == [("7000000001", 100)]
    assert written.value == 100
    assert written.previous_value == 75
    assert written.verified is True


def test_night_vision_write_maps_only_the_legacy_semantic_mode(monkeypatch):
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
        lambda _camera_id: enrollment,
    )
    monkeypatch.setattr(
        yoosee_controls,
        "run_with_fresh_access",
        lambda selected, operation: operation(selected),
    )

    def fake_write(selected, mode):
        observed.append((selected.device_id, mode))
        return P2PNightVisionWrite(selected.device_id, mode, 0, 2, True, True, 0, True)

    monkeypatch.setattr(yoosee_controls, "set_camera_night_vision", fake_write)

    result = _drv().write_control(camera, "night_vision", "night")

    assert observed == [("7000000001", "night")]
    assert result.value == "night"
    assert result.native_previous_value == 0
    assert result.native_requested_value == 2
    assert result.verified is True


@pytest.mark.parametrize("value", [True, 0, "auto", "day", "ir"])
def test_night_vision_rejects_values_outside_advertised_choices(monkeypatch, value):
    camera = Camera(
        mac="aa:bb:cc:dd:ee:01",
        camera_id="cam_0123456789abcdef01234567",
    )
    enrollment = P2PEnrollment(
        "7000000001", 123, bytes(range(64)), None, "now", "now", camera.camera_id
    )
    monkeypatch.setattr(
        yoosee_controls.p2p,
        "get_enrollment_for_camera",
        lambda _camera_id: enrollment,
    )

    with pytest.raises(yoosee_controls.ControlOperationError, match="automatic, daytime or night"):
        _drv().write_control(camera, "night_vision", value)


def test_smart_protection_read_and_write_use_semantic_booleans(monkeypatch):
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
        lambda _camera_id: enrollment,
    )
    monkeypatch.setattr(
        yoosee_controls,
        "run_with_fresh_access",
        lambda selected, operation: operation(selected),
    )
    monkeypatch.setattr(
        yoosee_controls,
        "read_camera_smart_protection",
        lambda selected: P2PSmartProtectionState(selected.device_id, True, True, True, True, 0),
    )

    def fake_write(selected, enabled):
        observed.append((selected.device_id, enabled))
        return P2PSmartProtectionWrite(selected.device_id, enabled, True, True, True, 0, True)

    monkeypatch.setattr(yoosee_controls, "set_camera_smart_protection", fake_write)

    read = _drv().read_control(camera, "smart_protection")
    written = _drv().write_control(camera, "smart_protection", False)

    assert read.value is True
    assert read.authenticated is True
    assert observed == [("7000000001", False)]
    assert written.value is False
    assert written.previous_value is True
    assert written.verified is True


def test_smart_protection_schedule_stays_typed_across_driver_boundary(monkeypatch):
    camera = Camera(
        mac="aa:bb:cc:dd:ee:01",
        camera_id="cam_0123456789abcdef01234567",
    )
    enrollment = P2PEnrollment(
        "7000000001", 123, bytes(range(64)), None, "now", "now", camera.camera_id
    )
    previous = WeeklySchedule("00:00", "00:00", ("sun", "mon", "tue", "wed", "thu", "fri", "sat"))
    requested = WeeklySchedule("22:30", "06:15", ("mon", "wed", "fri"))
    observed = []
    monkeypatch.setattr(
        yoosee_controls.p2p,
        "get_enrollment_for_camera",
        lambda _camera_id: enrollment,
    )
    monkeypatch.setattr(
        yoosee_controls,
        "run_with_fresh_access",
        lambda selected, operation: operation(selected),
    )
    monkeypatch.setattr(
        yoosee_controls,
        "read_camera_smart_protection_schedule",
        lambda selected: P2PSmartProtectionScheduleState(
            selected.device_id, previous, True, True, True, 0
        ),
    )

    def fake_write(selected, schedule):
        observed.append((selected.device_id, schedule))
        return P2PSmartProtectionScheduleWrite(
            selected.device_id, schedule, previous, True, True, 0, True
        )

    monkeypatch.setattr(yoosee_controls, "set_camera_smart_protection_schedule", fake_write)

    read = _drv().read_control(camera, "smart_protection_schedule")
    written = _drv().write_control(camera, "smart_protection_schedule", requested)

    assert read.value == previous
    assert observed == [("7000000001", requested)]
    assert written.value == requested
    assert written.previous_value == previous
    assert written.verified is True


def test_alarm_voice_options_and_write_resolve_only_a_fresh_catalog_resource(monkeypatch):
    camera = Camera(
        mac="aa:bb:cc:dd:ee:01",
        camera_id="cam_0123456789abcdef01234567",
    )
    enrollment = P2PEnrollment(
        "7000000001", 123, bytes(range(64)), None, "now", "now", camera.camera_id
    )
    resource_id = base64.b64encode(struct.pack("<II16s", 4, 7270, bytes(16))).decode()
    resource = AlarmVoiceResource(
        "system-7270", "Zumbido 1", 4500, "AMR", True, 7270, resource_id
    )
    catalog = P2PAlarmVoiceCatalog(enrollment.device_id, 1, 0, True, (resource,))
    selected: list[AlarmVoiceResource] = []
    monkeypatch.setattr(yoosee_controls.p2p, "get_enrollment_for_camera", lambda _id: enrollment)
    monkeypatch.setattr(
        yoosee_controls,
        "run_with_fresh_access",
        lambda selected_enrollment, operation: operation(selected_enrollment),
    )
    monkeypatch.setattr(
        yoosee_controls,
        "read_camera_alarm_voice_catalog",
        lambda _enrollment: catalog,
    )

    def fake_select(_enrollment, chosen):
        selected.append(chosen)
        return P2PAlarmVoiceWrite(
            enrollment.device_id, chosen.key, 2886, chosen.logical_number, True, True, 0, True
        )

    monkeypatch.setattr(yoosee_controls, "set_camera_alarm_voice_resource", fake_select)

    options = _drv().control_options(camera, "alarm_voice")
    written = _drv().write_control(camera, "alarm_voice", "system-7270")

    assert options[0].public() == {
        "value": "system-7270",
        "label": "Zumbido 1",
        "group": "system",
        "detail": "4.5 s",
    }
    assert selected == [resource]
    assert written.value == "system-7270"
    assert written.verified is True

    with pytest.raises(yoosee_controls.ControlOperationError, match="fresh camera catalogue"):
        _drv().write_control(camera, "alarm_voice", "system-9999")
