"""Driver-dispatched camera controls.

This is the application boundary between HTTP and camera-family implementations.  Routes select
only a public camera ID and semantic control key; the resolved driver owns every protocol detail.
"""

from __future__ import annotations

from .. import drivers
from ..db import registry
from ..db.registry import Camera
from ..drivers.contracts import ControlDescriptor, ControlOption, ControlResult, ControlValue


class CameraNotFound(LookupError):
    """The opaque public camera ID does not resolve to a registry camera."""


def _camera(camera_id: str) -> Camera:
    camera = registry.get_camera_by_id(camera_id)
    if camera is None:
        raise CameraNotFound("camera not found")
    return camera


def control_catalog(camera: Camera) -> dict[str, dict[str, object]]:
    """Return the selected driver's safe public control descriptors."""

    descriptors: tuple[ControlDescriptor, ...] = drivers.for_camera(camera).control_catalog(camera)
    return {descriptor.key: descriptor.public() for descriptor in descriptors}


def _operation(camera: Camera, key: str, permission: str):
    driver = drivers.for_camera(camera)
    descriptor = next(
        (item for item in driver.control_catalog(camera) if item.key == key),
        None,
    )
    if descriptor is None or not getattr(descriptor, permission):
        raise drivers.Unsupported(key)
    return driver


def read_control(camera_id: str, key: str) -> ControlResult:
    camera = _camera(camera_id)
    return _operation(camera, key, "readable").read_control(camera, key)


def control_options(camera_id: str, key: str) -> tuple[ControlOption, ...]:
    camera = _camera(camera_id)
    driver = drivers.for_camera(camera)
    descriptor = next(
        (item for item in driver.control_catalog(camera) if item.key == key),
        None,
    )
    if descriptor is None or descriptor.kind != "choice" or not descriptor.dynamic_options:
        raise drivers.Unsupported(key)
    return driver.control_options(camera, key)


def write_control(camera_id: str, key: str, value: ControlValue) -> ControlResult:
    camera = _camera(camera_id)
    return _operation(camera, key, "writable").write_control(camera, key, value)
