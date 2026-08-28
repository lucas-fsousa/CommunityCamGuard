"""Vendor-neutral, driver-dispatched camera control API."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Path, Response
from pydantic import BaseModel, ConfigDict

from ..auth import require_auth
from ..drivers import ControlNotReady, ControlOperationError, Unsupported
from ..drivers.contracts import ControlResult, ControlValue
from ..services import CameraNotFound, read_control, write_control
from .local_only import require_local_request

router = APIRouter(
    prefix="/api/cameras/{camera_id}/controls",
    dependencies=[Depends(require_auth), Depends(require_local_request)],
    tags=["cameras"],
)

CameraId = str
ControlKey = str


class ControlWriteIn(BaseModel):
    model_config = ConfigDict(strict=True)

    value: ControlValue


def _failure(exc: Exception) -> HTTPException:
    if isinstance(exc, CameraNotFound):
        return HTTPException(status_code=404, detail=str(exc))
    if isinstance(exc, Unsupported):
        return HTTPException(status_code=501, detail="this camera doesn't support that control")
    if isinstance(exc, ControlNotReady):
        return HTTPException(status_code=409, detail=str(exc))
    return HTTPException(status_code=502, detail=str(exc))


def _public(camera_id: str, result: ControlResult) -> dict[str, object]:
    """Project a driver result without exposing native values or transport coordinates."""

    return {
        "id": camera_id,
        "control": result.key,
        "value": result.value,
        "previous_value": result.previous_value,
        "changed": result.changed,
        "verified": result.verified,
        "authenticated": result.authenticated,
        "direct_connection": result.direct_connection,
        "transport_acknowledged": result.transport_acknowledged,
        "application_acknowledged": result.application_acknowledged,
        "error_code": result.error_code,
    }


@router.get("/{control_key}")
def read_camera_control(
    response: Response,
    camera_id: CameraId = Path(pattern=r"^cam_[0-9a-f]{24}$"),
    control_key: ControlKey = Path(pattern=r"^[a-z][a-z0-9_]{0,63}$"),
) -> dict[str, object]:
    """Read one semantic control explicitly advertised as readable by the selected driver."""

    try:
        result = read_control(camera_id, control_key)
    except (CameraNotFound, Unsupported, ControlNotReady, ControlOperationError) as exc:
        raise _failure(exc) from exc
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"
    return _public(camera_id, result)


@router.put("/{control_key}")
def write_camera_control(
    body: ControlWriteIn,
    response: Response,
    camera_id: CameraId = Path(pattern=r"^cam_[0-9a-f]{24}$"),
    control_key: ControlKey = Path(pattern=r"^[a-z][a-z0-9_]{0,63}$"),
) -> dict[str, object]:
    """Write one bounded semantic value explicitly advertised by the selected driver."""

    try:
        result = write_control(camera_id, control_key, body.value)
    except (CameraNotFound, Unsupported, ControlNotReady, ControlOperationError) as exc:
        raise _failure(exc) from exc
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"
    return _public(camera_id, result)
