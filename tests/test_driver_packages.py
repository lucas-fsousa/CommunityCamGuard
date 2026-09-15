"""Partial driver packages remain discovery-only; no network or hardware required."""

from importlib import import_module

import pytest

from backend.app import drivers
from backend.app.db.registry import Camera
from backend.app.drivers.base import Capabilities, Unsupported


@pytest.mark.parametrize("key,classname,vendor", [
    ("hikvision", "HikvisionDriver", "Hikvision"),
    ("dahua", "DahuaDriver", "Dahua Technology"),
    ("xiongmai", "XiongmaiDriver", "XMEye"),
])
def test_package_exports_registry_and_discovery_are_compatible(key, classname, vendor):
    package = import_module(f"backend.app.drivers.{key}")
    implementation = import_module(f"backend.app.drivers.{key}.driver")
    assert getattr(package, classname) is getattr(implementation, classname)
    assert isinstance(drivers.get(key), getattr(package, classname))
    assert drivers.detect(drivers.DetectContext(vendor=vendor)).key == key
    assert drivers.for_camera(Camera(capabilities={"driver": key})).key == key
    assert drivers.rtsp_paths_for(key, username="test-user", password="test-password")
    if key == "xiongmai":
        assert drivers.rtsp_paths_for(key) == []  # Credential-bearing paths stay gated.


@pytest.mark.parametrize("key", ["hikvision", "dahua", "xiongmai"])
def test_discovery_scaffolds_do_not_advertise_unimplemented_features(key):
    driver = drivers.get(key)
    camera = Camera(capabilities={"driver": key})
    caps = Capabilities(driver=key)
    driver._probe_controls(camera, caps)
    assert not caps.ptz and not caps.reboot
    assert driver.control_catalog(camera) == ()
    assert driver.onboarding() is None
    assert not driver.supports_audio_messages(camera)
    assert not driver.supports_audio_streams(camera)
    assert not driver.supports_onboard_recordings(camera)
    for operation in (lambda: driver.ptz(camera, "left"), lambda: driver.reboot(camera),
                      lambda: driver.write_control(camera, "white_light", True)):
        with pytest.raises(Unsupported):
            operation()


def test_multiple_brand_selection_does_not_replace_other_drivers():
    keys = ("hikvision", "yoosee", "dahua", "xiongmai", "generic")
    cameras = [Camera(camera_id=f"camera-{key}", capabilities={"driver": key}) for key in keys]
    assert [drivers.for_camera(camera).key for camera in cameras] == list(keys)
    assert drivers.DRIVERS[-1].key == "generic"
