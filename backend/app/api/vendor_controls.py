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
from ..drivers import (
    ControlNotReady,
    ControlOperationError,
    Unsupported,
)
from ..services import CameraNotFound, read_control, write_control
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


def _failure(exc: Exception) -> HTTPException:
    if isinstance(exc, CameraNotFound):
        return HTTPException(status_code=404, detail=str(exc))
    if isinstance(exc, Unsupported):
        return HTTPException(status_code=501, detail="this camera doesn't support that control")
    if isinstance(exc, ControlNotReady):
        return HTTPException(status_code=409, detail=str(exc))
    return HTTPException(status_code=502, detail=str(exc))


def _read(camera_id: str, key: str):
    try:
        return read_control(camera_id, key)
    except (CameraNotFound, Unsupported, ControlNotReady, ControlOperationError) as exc:
        raise _failure(exc) from exc


def _write(camera_id: str, key: str, value: bool | str):
    try:
        return write_control(camera_id, key, value)
    except (CameraNotFound, Unsupported, ControlNotReady, ControlOperationError) as exc:
        raise _failure(exc) from exc


@router.get("/{camera_id}/white-light")
def white_light_state(
    response: Response,
    camera_id: CameraId = Path(pattern=r"^cam_[0-9a-f]{24}$"),
) -> dict:
    """Read the selected enrolled camera's physical white-floodlight state."""

    result = _read(camera_id, "white_light")
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"
    return {
        "id": camera_id,
        "enabled": result.value,
        "authenticated": result.authenticated,
        "direct_handshake": result.direct_connection,
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

    result = _write(camera_id, "white_light", body.enabled)
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"
    return {
        "id": camera_id,
        "enabled": result.value,
        "previous_enabled": result.previous_value,
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

    result = _write(camera_id, "orientation", body.orientation)
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"
    return {
        "id": camera_id,
        "orientation": result.value,
        "previous_value": result.native_previous_value,
        "requested_value": result.native_requested_value,
        "changed": result.changed,
        "transport_acknowledged": result.transport_acknowledged,
        "error_code": result.error_code,
        "verified": result.verified,
    }
