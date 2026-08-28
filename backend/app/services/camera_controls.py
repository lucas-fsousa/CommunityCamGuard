"""Driver-dispatched camera controls.

This is the application boundary between HTTP and camera-family implementations.  Routes select
only a public camera ID and semantic control key; the resolved driver owns every protocol detail.
"""

from __future__ import annotations

from .. import drivers
from ..db import registry
from ..db.registry import Camera
from ..drivers.contracts import ControlDescriptor, ControlResult, ControlValue


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


def read_control(camera_id: str, key: str) -> ControlResult:
    camera = _camera(camera_id)
    return drivers.for_camera(camera).read_control(camera, key)


def write_control(camera_id: str, key: str, value: ControlValue) -> ControlResult:
    camera = _camera(camera_id)
    return drivers.for_camera(camera).write_control(camera, key, value)
