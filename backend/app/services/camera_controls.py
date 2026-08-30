"""Driver-dispatched camera controls.

This is the application boundary between HTTP and camera-family implementations.  Routes select
only a public camera ID and semantic control key; the resolved driver owns every protocol detail.
"""

from __future__ import annotations

import threading
from contextlib import contextmanager

from .. import drivers
from ..db import registry
from ..db.registry import Camera
from ..drivers.contracts import ControlDescriptor, ControlOption, ControlResult, ControlValue


class CameraNotFound(LookupError):
    """The opaque public camera ID does not resolve to a registry camera."""


class ControlBusy(RuntimeError):
    """Another control operation already owns this camera's constrained session."""


_locks_guard = threading.Lock()
_camera_locks: dict[str, threading.Lock] = {}


def _control_lock(camera_id: str) -> threading.Lock:
    with _locks_guard:
        return _camera_locks.setdefault(camera_id, threading.Lock())


@contextmanager
def _exclusive(camera_id: str):
    lock = _control_lock(camera_id)
    if not lock.acquire(blocking=False):
        raise ControlBusy("another control operation is already running for this camera")
    try:
        yield
    finally:
        lock.release()


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
    with _exclusive(camera_id):
        return _operation(camera, key, "readable").read_control(camera, key)


def control_options(camera_id: str, key: str) -> tuple[ControlOption, ...]:
    camera = _camera(camera_id)
    with _exclusive(camera_id):
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
    with _exclusive(camera_id):
        return _operation(camera, key, "writable").write_control(camera, key, value)
