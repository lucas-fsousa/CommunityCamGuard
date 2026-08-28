"""Yoosee implementations of vendor-neutral semantic camera controls.

This adapter is the only driver-layer module that knows how an opaque camera association becomes
Gwell P2P enrollment material. Protocol modules stay typed and bounded inside this driver package;
HTTP and the generic application service never import them directly.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ...db import p2p
from ..base import Unsupported
from ..contracts import (
    ControlDescriptor,
    ControlNotReady,
    ControlOperationError,
    ControlResult,
    ControlValue,
)
from .p2p import (
    P2PProbeError,
    read_camera_white_light,
    run_with_fresh_access,
    set_camera_orientation,
    set_camera_white_light,
)

if TYPE_CHECKING:
    from ...db.p2p import P2PEnrollment
    from ...db.registry import Camera

WHITE_LIGHT = "white_light"
ORIENTATION = "orientation"

_DESCRIPTORS = (
    ControlDescriptor(WHITE_LIGHT, "boolean", readable=True, writable=True),
    ControlDescriptor(
        ORIENTATION,
        "choice",
        writable=True,
        options=("normal", "inverted"),
    ),
)


def catalog(camera: Camera) -> tuple[ControlDescriptor, ...]:
    """Advertise controls only when this exact camera has durable Yoosee material."""

    if not camera.camera_id or not p2p.has_enrollment_for_camera(camera.camera_id):
        return ()
    return _DESCRIPTORS


def _enrollment(camera: Camera) -> P2PEnrollment:
    if not camera.camera_id:
        raise ControlNotReady("camera has no stable public identity")
    enrollment = p2p.get_enrollment_for_camera(camera.camera_id)
    if enrollment is None:
        raise ControlNotReady("camera has no linked Yoosee P2P enrollment")
    return enrollment


def read(camera: Camera, key: str) -> ControlResult:
    if key != WHITE_LIGHT:
        raise Unsupported(key)
    try:
        result = run_with_fresh_access(_enrollment(camera), read_camera_white_light)
    except P2PProbeError as exc:
        raise ControlOperationError(str(exc)) from exc
    return ControlResult(
        key=key,
        value=result.enabled,
        verified=result.application_acknowledged,
        authenticated=result.authenticated,
        direct_connection=result.direct_handshake,
        transport_acknowledged=result.transport_acknowledged,
        application_acknowledged=result.application_acknowledged,
    )


def write(camera: Camera, key: str, value: ControlValue) -> ControlResult:
    enrollment = _enrollment(camera)
    try:
        if key == WHITE_LIGHT:
            if type(value) is not bool:
                raise ValueError("white-light state must be a boolean")
            light_result = run_with_fresh_access(
                enrollment,
                lambda selected: set_camera_white_light(selected, value),
            )
            return ControlResult(
                key=key,
                value=light_result.enabled,
                previous_value=light_result.previous_enabled,
                changed=light_result.changed,
                verified=light_result.verified,
                transport_acknowledged=light_result.transport_acknowledged,
                application_acknowledged=light_result.application_acknowledged,
            )
        if key == ORIENTATION:
            if not isinstance(value, str) or value not in {"normal", "inverted"}:
                raise ValueError("orientation must be normal or inverted")
            orientation_result = run_with_fresh_access(
                enrollment,
                lambda selected: set_camera_orientation(selected, value),
            )
            return ControlResult(
                key=key,
                value=orientation_result.orientation,
                changed=orientation_result.changed,
                verified=orientation_result.verified,
                transport_acknowledged=orientation_result.transport_acknowledged,
                error_code=orientation_result.error_code,
                native_previous_value=orientation_result.previous_value,
                native_requested_value=orientation_result.requested_value,
            )
    except P2PProbeError as exc:
        raise ControlOperationError(str(exc)) from exc
    raise Unsupported(key)
