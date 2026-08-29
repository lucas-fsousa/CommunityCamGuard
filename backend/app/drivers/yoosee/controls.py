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
    WeeklySchedule,
)
from .p2p import (
    NIGHT_VISION_VALUES,
    P2PProbeError,
    pulse_camera_siren,
    read_camera_smart_protection,
    read_camera_smart_protection_schedule,
    read_camera_speaker_volume,
    read_camera_white_light,
    run_with_fresh_access,
    set_camera_night_vision,
    set_camera_orientation,
    set_camera_smart_protection,
    set_camera_smart_protection_schedule,
    set_camera_speaker_volume,
    set_camera_white_light,
)

if TYPE_CHECKING:
    from ...db.p2p import P2PEnrollment
    from ...db.registry import Camera

WHITE_LIGHT = "white_light"
ORIENTATION = "orientation"
SIREN_PULSE = "siren_pulse"
SPEAKER_VOLUME = "speaker_volume"
NIGHT_VISION = "night_vision"
SMART_PROTECTION = "smart_protection"
SMART_PROTECTION_SCHEDULE = "smart_protection_schedule"

_DESCRIPTORS = (
    ControlDescriptor(WHITE_LIGHT, "boolean", readable=True, writable=True),
    ControlDescriptor(
        ORIENTATION,
        "choice",
        writable=True,
        options=("normal", "inverted"),
    ),
    ControlDescriptor(
        SIREN_PULSE,
        "action",
        writable=True,
        options=("2", "5", "10"),
    ),
    ControlDescriptor(
        SPEAKER_VOLUME,
        "choice",
        readable=True,
        writable=True,
        options=("0", "25", "50", "75", "100"),
    ),
    ControlDescriptor(
        NIGHT_VISION,
        "choice",
        writable=True,
        options=tuple(NIGHT_VISION_VALUES),
    ),
    ControlDescriptor(SMART_PROTECTION, "boolean", readable=True, writable=True),
    ControlDescriptor(
        SMART_PROTECTION_SCHEDULE,
        "weekly_schedule",
        readable=True,
        writable=True,
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
    if key not in {
        WHITE_LIGHT,
        SPEAKER_VOLUME,
        SMART_PROTECTION,
        SMART_PROTECTION_SCHEDULE,
    }:
        raise Unsupported(key)
    try:
        enrollment = _enrollment(camera)
        if key == WHITE_LIGHT:
            result = run_with_fresh_access(enrollment, read_camera_white_light)
            return ControlResult(
                key=key,
                value=result.enabled,
                verified=result.application_acknowledged,
                authenticated=result.authenticated,
                direct_connection=result.direct_handshake,
                transport_acknowledged=result.transport_acknowledged,
                application_acknowledged=result.application_acknowledged,
            )
        if key == SMART_PROTECTION:
            protection_result = run_with_fresh_access(enrollment, read_camera_smart_protection)
            return ControlResult(
                key=key,
                value=protection_result.enabled,
                verified=protection_result.error_code == 0,
                authenticated=protection_result.authenticated,
                direct_connection=protection_result.direct_handshake,
                transport_acknowledged=protection_result.transport_acknowledged,
                error_code=protection_result.error_code,
            )
        if key == SMART_PROTECTION_SCHEDULE:
            schedule_result = run_with_fresh_access(
                enrollment, read_camera_smart_protection_schedule
            )
            return ControlResult(
                key=key,
                value=schedule_result.schedule,
                verified=schedule_result.error_code == 0,
                authenticated=schedule_result.authenticated,
                direct_connection=schedule_result.direct_handshake,
                transport_acknowledged=schedule_result.transport_acknowledged,
                error_code=schedule_result.error_code,
            )
        volume_result = run_with_fresh_access(enrollment, read_camera_speaker_volume)
        return ControlResult(
            key=key,
            value=volume_result.volume_percent,
            verified=volume_result.error_code == 0,
            authenticated=volume_result.authenticated,
            direct_connection=volume_result.direct_handshake,
            transport_acknowledged=volume_result.transport_acknowledged,
            error_code=volume_result.error_code,
            native_previous_value=volume_result.raw_value,
        )
    except P2PProbeError as exc:
        raise ControlOperationError(str(exc)) from exc


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
        if key == SIREN_PULSE:
            if type(value) is not int or value not in {2, 5, 10}:
                raise ValueError("siren pulse must be 2, 5 or 10 seconds")
            pulse_result = run_with_fresh_access(
                enrollment,
                lambda selected: pulse_camera_siren(selected, value),
            )
            return ControlResult(
                key=key,
                value=pulse_result.duration_seconds,
                changed=True,
                verified=pulse_result.final_off_confirmed,
                transport_acknowledged=(
                    pulse_result.enable_transport_acknowledged
                    and pulse_result.disable_transport_acknowledged
                ),
                application_acknowledged=(
                    pulse_result.enable_error_code == 0 and pulse_result.disable_error_code == 0
                ),
                error_code=(
                    pulse_result.disable_error_code
                    if pulse_result.disable_error_code is not None
                    else pulse_result.enable_error_code
                ),
            )
        if key == SPEAKER_VOLUME:
            if type(value) is not int or value not in {0, 25, 50, 75, 100}:
                raise ValueError("speaker volume must be 0, 25, 50, 75 or 100 percent")
            volume_result = run_with_fresh_access(
                enrollment,
                lambda selected: set_camera_speaker_volume(selected, value),
            )
            return ControlResult(
                key=key,
                value=volume_result.volume_percent,
                previous_value=volume_result.previous_percent,
                changed=volume_result.changed,
                verified=volume_result.verified,
                transport_acknowledged=volume_result.transport_acknowledged,
                error_code=volume_result.error_code,
                native_previous_value=volume_result.previous_raw,
                native_requested_value=volume_result.requested_raw,
            )
        if key == NIGHT_VISION:
            if not isinstance(value, str) or value not in NIGHT_VISION_VALUES:
                raise ValueError("night-vision mode must be automatic, daytime or night")
            night_result = run_with_fresh_access(
                enrollment,
                lambda selected: set_camera_night_vision(selected, value),
            )
            return ControlResult(
                key=key,
                value=night_result.mode,
                changed=night_result.changed,
                verified=night_result.verified,
                transport_acknowledged=night_result.transport_acknowledged,
                error_code=night_result.error_code,
                native_previous_value=night_result.previous_value,
                native_requested_value=night_result.requested_value,
            )
        if key == SMART_PROTECTION:
            if type(value) is not bool:
                raise ValueError("smart-protection state must be a boolean")
            protection_result = run_with_fresh_access(
                enrollment,
                lambda selected: set_camera_smart_protection(selected, value),
            )
            return ControlResult(
                key=key,
                value=protection_result.enabled,
                previous_value=protection_result.previous_enabled,
                changed=protection_result.changed,
                verified=protection_result.verified,
                transport_acknowledged=protection_result.transport_acknowledged,
                error_code=protection_result.error_code,
            )
        if key == SMART_PROTECTION_SCHEDULE:
            if not isinstance(value, WeeklySchedule):
                raise ValueError("smart-protection schedule must be a weekly schedule")
            schedule_result = run_with_fresh_access(
                enrollment,
                lambda selected: set_camera_smart_protection_schedule(selected, value),
            )
            return ControlResult(
                key=key,
                value=schedule_result.schedule,
                previous_value=schedule_result.previous_schedule,
                changed=schedule_result.changed,
                verified=schedule_result.verified,
                transport_acknowledged=schedule_result.transport_acknowledged,
                error_code=schedule_result.error_code,
            )
    except (P2PProbeError, ValueError) as exc:
        raise ControlOperationError(str(exc)) from exc
    raise Unsupported(key)
