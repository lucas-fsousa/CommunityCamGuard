from __future__ import annotations

import pytest

from backend.app import drivers
from backend.app.camera_identity import stable_camera_id
from backend.app.db import registry
from backend.app.drivers.base import CameraDriver, Unsupported
from backend.app.drivers.contracts import ControlDescriptor, ControlOption, ControlResult
from backend.app.services import camera_controls

CAMERA_ID = stable_camera_id("mac", "aa:bb:cc:dd:ee:03")


class FakeControlDriver(CameraDriver):
    key = "fake-control"

    def control_catalog(self, camera):
        return (ControlDescriptor("white_light", "boolean", True, True),)

    def read_control(self, camera, key):
        return ControlResult(key, True, verified=True)

    def write_control(self, camera, key, value):
        return ControlResult(key, value, previous_value=False, changed=True, verified=True)


def _camera(driver="fake-control"):
    return registry.Camera(
        mac="aa:bb:cc:dd:ee:03",
        camera_id=CAMERA_ID,
        capabilities={"driver": driver},
    )


def test_catalog_and_operations_are_dispatched_to_selected_driver(monkeypatch):
    selected = FakeControlDriver()
    monkeypatch.setattr(drivers, "for_camera", lambda camera: selected)
    monkeypatch.setattr(registry, "get_camera_by_id", lambda camera_id: _camera())

    catalog = camera_controls.control_catalog(_camera())
    read = camera_controls.read_control(CAMERA_ID, "white_light")
    written = camera_controls.write_control(CAMERA_ID, "white_light", False)

    assert catalog == {
        "white_light": {
            "kind": "boolean",
            "readable": True,
            "writable": True,
            "options": [],
            "dynamic_options": False,
        }
    }
    assert read.value is True and read.verified is True
    assert written.value is False and written.changed is True


def test_unknown_camera_is_rejected_before_driver_selection(monkeypatch):
    monkeypatch.setattr(registry, "get_camera_by_id", lambda _camera_id: None)
    monkeypatch.setattr(
        drivers,
        "for_camera",
        lambda _camera: (_ for _ in ()).throw(AssertionError("driver selected")),
    )

    with pytest.raises(camera_controls.CameraNotFound):
        camera_controls.read_control(CAMERA_ID, "white_light")


def test_generic_driver_never_inherits_controls_from_vendor_enrollment(monkeypatch):
    camera = _camera("generic")
    monkeypatch.setattr(
        registry,
        "get_camera_by_id",
        lambda _camera_id: camera,
    )

    assert camera_controls.control_catalog(camera) == {}
    with pytest.raises(Unsupported):
        camera_controls.write_control(CAMERA_ID, "white_light", True)


def test_operation_must_be_advertised_with_matching_permission(monkeypatch):
    class WriteOnlyDriver(FakeControlDriver):
        def control_catalog(self, camera):
            return (ControlDescriptor("orientation", "choice", False, True),)

    monkeypatch.setattr(drivers, "for_camera", lambda camera: WriteOnlyDriver())
    monkeypatch.setattr(registry, "get_camera_by_id", lambda camera_id: _camera())

    with pytest.raises(Unsupported):
        camera_controls.read_control(CAMERA_ID, "orientation")
    with pytest.raises(Unsupported):
        camera_controls.write_control(CAMERA_ID, "hidden_native_path", True)


def test_dynamic_options_require_an_explicit_choice_descriptor(monkeypatch):
    class DynamicDriver(FakeControlDriver):
        def control_catalog(self, camera):
            return (ControlDescriptor("alarm_voice", "choice", writable=True, dynamic_options=True),)

        def control_options(self, camera, key):
            return (ControlOption("system-1", "Tone", "system", "1 s"),)

    monkeypatch.setattr(drivers, "for_camera", lambda camera: DynamicDriver())
    monkeypatch.setattr(registry, "get_camera_by_id", lambda camera_id: _camera())

    assert camera_controls.control_options(CAMERA_ID, "alarm_voice") == (
        ControlOption("system-1", "Tone", "system", "1 s"),
    )
    with pytest.raises(Unsupported):
        camera_controls.control_options(CAMERA_ID, "white_light")


def test_overlapping_operations_on_one_camera_fail_busy_without_driver_dispatch(monkeypatch):
    monkeypatch.setattr(registry, "get_camera_by_id", lambda camera_id: _camera())
    monkeypatch.setattr(
        drivers,
        "for_camera",
        lambda _camera: (_ for _ in ()).throw(AssertionError("driver must not be reached")),
    )
    lock = camera_controls._control_lock(CAMERA_ID)
    lock.acquire()
    try:
        with pytest.raises(camera_controls.ControlBusy, match="already running"):
            camera_controls.read_control(CAMERA_ID, "white_light")
    finally:
        lock.release()
