"""Authenticated LAN-only API for typed proprietary camera controls.

Keeping this router separate prevents the provisioning/media route module from becoming the home
of every recovered vendor feature.  Each operation delegates to a bounded feature module; no raw
P2P path or passthrough payload is accepted from clients.
"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Path, Response
from pydantic import BaseModel

from ..auth import require_auth
from ..db import registry
from ..provisioning import (
    PrivilegedEnrollmentError,
    bound_privileged_enrollment_for_camera,
)
from ..vendor_p2p import (
    P2PProbeError,
    read_camera_white_light,
    run_with_fresh_access,
    set_camera_orientation,
    set_camera_white_light,
)
from .local_only import require_local_request

router = APIRouter(
    prefix="/api/vendor-controls",
    dependencies=[Depends(require_auth), Depends(require_local_request)],
    tags=["vendor controls"],
)

CameraId = str


class WhiteLightIn(BaseModel):
    enabled: bool


class OrientationIn(BaseModel):
    orientation: Literal["normal", "inverted"]


def _enrollment(camera_id: str):
    if registry.get_camera_by_id(camera_id) is None:
        raise HTTPException(status_code=404, detail="camera not found")
    try:
        return bound_privileged_enrollment_for_camera(camera_id)
    except PrivilegedEnrollmentError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/{camera_id}/white-light")
def white_light_state(
    response: Response,
    camera_id: CameraId = Path(pattern=r"^cam_[0-9a-f]{24}$"),
) -> dict:
    """Read the selected enrolled camera's physical white-floodlight state."""

    try:
        result = run_with_fresh_access(_enrollment(camera_id), read_camera_white_light)
    except P2PProbeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"
    return {
        "id": camera_id,
        "enabled": result.enabled,
        "authenticated": result.authenticated,
        "direct_handshake": result.direct_handshake,
        "transport_acknowledged": result.transport_acknowledged,
        "application_acknowledged": result.application_acknowledged,
    }


@router.put("/{camera_id}/white-light")
def update_white_light(
    body: WhiteLightIn,
    response: Response,
    camera_id: CameraId = Path(pattern=r"^cam_[0-9a-f]{24}$"),
) -> dict:
    """Set ON/OFF through the typed, preflighted and readback-verified operation."""

    try:
        result = run_with_fresh_access(
            _enrollment(camera_id),
            lambda enrollment: set_camera_white_light(enrollment, body.enabled),
        )
    except P2PProbeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"
    return {
        "id": camera_id,
        "enabled": result.enabled,
        "previous_enabled": result.previous_enabled,
        "changed": result.changed,
        "transport_acknowledged": result.transport_acknowledged,
        "application_acknowledged": result.application_acknowledged,
        "verified": result.verified,
    }


@router.put("/{camera_id}/orientation")
def update_orientation(
    body: OrientationIn,
    response: Response,
    camera_id: CameraId = Path(pattern=r"^cam_[0-9a-f]{24}$"),
) -> dict:
    """Set normal/180° image orientation through the fixed, typed D2 property."""

    try:
        result = run_with_fresh_access(
            _enrollment(camera_id),
            lambda enrollment: set_camera_orientation(enrollment, body.orientation),
        )
    except P2PProbeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"
    return {
        "id": camera_id,
        "orientation": result.orientation,
        "previous_value": result.previous_value,
        "requested_value": result.requested_value,
        "changed": result.changed,
        "transport_acknowledged": result.transport_acknowledged,
        "error_code": result.error_code,
        "verified": result.verified,
    }
