"""LAN-only completion of a bound factory camera into a verified registry camera."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field

from ..auth import require_auth
from ..provisioning import (
    LabelError,
    OnboardingCompletionError,
    PrivilegedEnrollmentError,
    bound_privileged_enrollment,
    complete_camera_onboarding,
    inspect_label,
    locate_camera_by_mac,
)
from .local_only import require_local_request

log = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/provisioning/privileged",
    dependencies=[Depends(require_auth), Depends(require_local_request)],
    tags=["provisioning"],
)


class CompleteOnboardingIn(BaseModel):
    label: str = Field(default="", max_length=512)
    device_id: str = Field(default="", max_length=20)
    capability_code: str = Field(default="", max_length=10)
    firmware_version: str = Field(default="", max_length=64)
    mac: str = Field(default="", max_length=32)
    name: str = Field(default="", max_length=80)


def _resync_services(request: Request) -> None:
    media = getattr(request.app.state, "media", None)
    recorder = getattr(request.app.state, "rec", None)
    try:
        if media is not None:
            media.restart()
            media.wait_healthy(timeout=6)
        if recorder is not None:
            recorder.start()
    except Exception as exc:  # registry commit already succeeded; startup will reconcile later
        log.warning("service resync after onboarding failed: %s", exc)


@router.post("/complete")
def complete_onboarding(
    body: CompleteOnboardingIn,
    request: Request,
    response: Response,
) -> dict:
    """Finish P2P → RTSP → authenticated media → registry as one guarded operation."""

    try:
        identity = inspect_label(
            label=body.label,
            device_id=body.device_id,
            capability_code=body.capability_code,
            firmware_version=body.firmware_version,
            mac=body.mac,
        )
    except LabelError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if not identity["mac"]:
        raise HTTPException(
            status_code=422,
            detail="the printed MAC address is required to locate and register this camera",
        )
    try:
        enrollment = bound_privileged_enrollment(identity["device_id"])
        located = locate_camera_by_mac(identity["mac"])
        completed = complete_camera_onboarding(
            enrollment,
            located,
            device_id=identity["device_id"],
            name=body.name,
            firmware_hint=identity["firmware_version"],
        )
    except PrivilegedEnrollmentError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except OnboardingCompletionError as exc:
        raise HTTPException(status_code=502, detail=f"{exc.stage}: {exc}") from exc
    _resync_services(request)
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"
    camera = completed.camera
    return {
        "status": "configured",
        "camera": {
            "id": camera.camera_id,
            "name": camera.name,
            "last_ip": camera.last_ip,
            "stream_path": camera.stream_path,
        },
        "media": {
            "transport": completed.proof.transport,
            "has_video": completed.proof.has_video,
            "has_audio": completed.proof.has_audio,
            "video_codec": completed.proof.video_codec,
            "audio_codec": completed.proof.audio_codec,
        },
        "stages": list(completed.stages),
        "already_configured": completed.already_configured,
    }
