from __future__ import annotations

import pytest

from backend.app import drivers
from backend.app.camera_identity import stable_camera_id
from backend.app.db import registry
from backend.app.drivers.base import CameraDriver, Unsupported
from backend.app.drivers.contracts import ControlDescriptor, ControlResult
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
