"""Configured camera CRUD, status, capability probe and generic controls."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from .. import drivers
from ..auth import require_auth
from ..db import registry
from ..discovery import rtsp
from ..services.camera_runtime import (
    probe_and_store,
    resolve_camera,
    resync_services,
    runtime_statuses,
)
from .camera_presenter import camera_out

router = APIRouter(prefix="/api", tags=["cameras"])
log = logging.getLogger(__name__)


class CameraIn(BaseModel):
    mac: str
    name: str | None = None
    username: str | None = None
    password: str | None = None
    stream_path: str | None = None
    rtsp_port: int | None = None
    last_ip: str | None = None
    vendor: str | None = None


class PtzIn(BaseModel):
    direction: str | None = None
    action: str = "step"


@router.get("/cameras", dependencies=[Depends(require_auth)])
def list_cameras(request: Request) -> list[dict]:
    cameras = registry.list_cameras()
    statuses = {item["id"]: item for item in runtime_statuses(request, cameras)}
    result = []
    for camera in cameras:
        item = camera_out(camera)
        item.update(statuses[camera.camera_id])
        result.append(item)
    return result


@router.get("/cameras/status", dependencies=[Depends(require_auth)])
def camera_statuses(request: Request) -> list[dict]:
    """Return the small polling surface for online and recording indicators."""

    return runtime_statuses(request, registry.list_cameras())


@router.post("/cameras", dependencies=[Depends(require_auth)])
def upsert_camera(body: CameraIn, request: Request) -> dict:
    if body.last_ip:
        credential_status = rtsp.check_credentials(
            body.last_ip,
            body.rtsp_port or registry.DEFAULT_RTSP_PORT,
            body.stream_path or "/onvif1",
            body.username or "",
            body.password or "",
        )
        if credential_status == "auth":
            raise HTTPException(
                status_code=422,
                detail="camera rejected these credentials (wrong username or password)",
            )
    camera = registry.upsert_camera(
        body.mac,
        name=body.name,
        username=body.username,
        password=body.password,
        stream_path=body.stream_path,
        rtsp_port=body.rtsp_port,
        last_ip=body.last_ip,
        vendor=body.vendor,
    )
    if camera.last_ip:
        try:
            camera = probe_and_store(camera)
        except Exception as exc:
            log.warning("capability probe on add failed for %s: %s", camera.mac, exc)
    resync_services(request)
    return camera_out(camera)


@router.delete("/cameras/{camera_id}", dependencies=[Depends(require_auth)])
def delete_camera(camera_id: str, request: Request) -> dict:
    camera = resolve_camera(camera_id)
    if camera is None:
        raise HTTPException(status_code=404, detail="camera not found")
    registry.delete_camera_by_id(camera.camera_id)
    resync_services(request)
    return {"ok": True}


@router.post("/cameras/{camera_id}/probe", dependencies=[Depends(require_auth)])
def probe_camera(camera_id: str) -> dict:
    """Detect the camera driver and persist its live capabilities."""

    camera = resolve_camera(camera_id)
    if camera is None:
        raise HTTPException(status_code=404, detail="camera not found")
    if not camera.last_ip:
        raise HTTPException(status_code=409, detail="camera has no known IP; run a scan first")
    return camera_out(probe_and_store(camera))


@router.post("/cameras/{camera_id}/ptz", dependencies=[Depends(require_auth)])
def ptz_move(camera_id: str, body: PtzIn) -> dict:
    """Pan or tilt through the camera's selected driver."""

    camera = resolve_camera(camera_id)
    if camera is None:
        raise HTTPException(status_code=404, detail="camera not found")
    try:
        ok = drivers.for_camera(camera).ptz(
            camera,
            body.direction,
            (body.action or "step").lower(),
        )
    except drivers.Unsupported as exc:
        raise HTTPException(status_code=501, detail="this camera doesn't support PTZ") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not ok:
        raise HTTPException(status_code=502, detail="camera did not accept the PTZ command")
    return {
        "ok": True,
        "action": (body.action or "step").lower(),
        "direction": (body.direction or "").lower(),
    }


@router.post("/cameras/{camera_id}/reboot", dependencies=[Depends(require_auth)])
def reboot_camera(camera_id: str) -> dict:
    """Reboot through the selected driver when that operation is supported."""

    camera = resolve_camera(camera_id)
    if camera is None:
        raise HTTPException(status_code=404, detail="camera not found")
    try:
        ok = drivers.for_camera(camera).reboot(camera)
    except drivers.Unsupported as exc:
        raise HTTPException(
            status_code=501,
            detail="this camera doesn't support software reboot",
        ) from exc
    if not ok:
        raise HTTPException(status_code=502, detail="camera did not accept the reboot command")
    return {"ok": True, "rebooting": True}
