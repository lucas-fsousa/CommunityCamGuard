"""Vendor-neutral, driver-dispatched camera control API."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Path, Response
from pydantic import BaseModel, ConfigDict, Field, field_validator

from ..auth import require_auth
from ..drivers import ControlNotReady, ControlOperationError, Unsupported
from ..drivers.contracts import (
    ControlResult,
    ControlValue,
    Weekday,
    WeeklySchedule,
    public_control_value,
)
from ..services import CameraNotFound, control_options, read_control, write_control
from .local_only import require_local_request

router = APIRouter(
    prefix="/api/cameras/{camera_id}/controls",
    dependencies=[Depends(require_auth), Depends(require_local_request)],
    tags=["cameras"],
)

CameraId = str
ControlKey = str


class WeeklyScheduleIn(BaseModel):
    model_config = ConfigDict(strict=True)

    start: str = Field(pattern=r"^(?:[01][0-9]|2[0-3]):[0-5][0-9]$")
    end: str = Field(pattern=r"^(?:[01][0-9]|2[0-3]):[0-5][0-9]$")
    weekdays: list[Weekday] = Field(min_length=1, max_length=7)

    @field_validator("weekdays")
    @classmethod
    def unique_weekdays(cls, weekdays: list[Weekday]) -> list[Weekday]:
        if len(set(weekdays)) != len(weekdays):
            raise ValueError("weekdays must be unique")
        return weekdays

    def contract(self) -> WeeklySchedule:
        return WeeklySchedule(self.start, self.end, tuple(self.weekdays))


class ControlWriteIn(BaseModel):
    model_config = ConfigDict(strict=True)

    value: bool | int | str | WeeklyScheduleIn

    def contract_value(self) -> ControlValue:
        return self.value.contract() if isinstance(self.value, WeeklyScheduleIn) else self.value


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
        "value": public_control_value(result.value),
        "previous_value": public_control_value(result.previous_value),
        "changed": result.changed,
        "verified": result.verified,
        "authenticated": result.authenticated,
        "direct_connection": result.direct_connection,
        "transport_acknowledged": result.transport_acknowledged,
        "application_acknowledged": result.application_acknowledged,
        "error_code": result.error_code,
    }


@router.get("/{control_key}/options")
def read_camera_control_options(
    response: Response,
    camera_id: CameraId = Path(pattern=r"^cam_[0-9a-f]{24}$"),
    control_key: ControlKey = Path(pattern=r"^[a-z][a-z0-9_]{0,63}$"),
) -> dict[str, object]:
    """Read sanitized options for one explicitly advertised dynamic choice."""

    try:
        options = control_options(camera_id, control_key)
    except (CameraNotFound, Unsupported, ControlNotReady, ControlOperationError) as exc:
        raise _failure(exc) from exc
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"
    return {
        "id": camera_id,
        "control": control_key,
        "options": [option.public() for option in options],
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
        result = write_control(camera_id, control_key, body.contract_value())
    except (CameraNotFound, Unsupported, ControlNotReady, ControlOperationError) as exc:
        raise _failure(exc) from exc
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"
    return _public(camera_id, result)
