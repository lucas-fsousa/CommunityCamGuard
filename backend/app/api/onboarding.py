"""LAN-only completion of a bound factory camera into a verified registry camera."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import Field

from ..auth import require_auth
from ..drivers.onboarding import (
    OnboardingCompletionError,
    OnboardingStateError,
)
from ..services.camera_runtime import resync_services
from .local_only import require_local_request
from .provisioning_common import (
    ProvisioningLabelIn,
    inspect_provisioning_label,
    onboarding,
)

router = APIRouter(
    prefix="/api/provisioning/privileged",
    dependencies=[Depends(require_auth), Depends(require_local_request)],
    tags=["provisioning"],
)


class CompleteOnboardingIn(ProvisioningLabelIn):
    name: str = Field(default="", max_length=80)


@router.post("/complete")
def complete_onboarding(
    body: CompleteOnboardingIn,
    request: Request,
    response: Response,
) -> dict:
    """Finish P2P → RTSP → authenticated media → registry as one guarded operation."""

    identity = inspect_provisioning_label(body)
    if not identity["mac"]:
        raise HTTPException(
            status_code=422,
            detail="the printed MAC address is required to locate and register this camera",
        )
    try:
        completed = onboarding().complete(
            device_id=identity["device_id"],
            mac=identity["mac"],
            name=body.name,
            firmware_hint=identity["firmware_version"],
        )
    except OnboardingStateError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except OnboardingCompletionError as exc:
        raise HTTPException(status_code=502, detail=f"{exc.stage}: {exc}") from exc
    resync_services(request)
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
