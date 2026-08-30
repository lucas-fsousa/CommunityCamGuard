"""Vendor-neutral bounded server-to-camera audio-message endpoint."""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, HTTPException, Path, Request, Response

from ..auth import require_auth
from ..drivers import ControlNotReady, ControlOperationError, Unsupported
from ..services import CameraNotFound, ControlBusy, send_audio_message
from .local_only import require_local_request

MAX_PCM_BYTES = 160_000  # Ten seconds of mono 8 kHz signed 16-bit PCM.
PCM_FRAME_BYTES = 320  # One 20 ms AMR-NB input frame.

router = APIRouter(
    prefix="/api/cameras/{camera_id}/intercom",
    dependencies=[Depends(require_auth), Depends(require_local_request)],
    tags=["cameras"],
)


def _failure(exc: Exception) -> HTTPException:
    if isinstance(exc, CameraNotFound):
        return HTTPException(status_code=404, detail=str(exc))
    if isinstance(exc, Unsupported):
        return HTTPException(status_code=501, detail="this camera doesn't support audio messages")
    if isinstance(exc, (ControlNotReady, ControlBusy)):
        return HTTPException(status_code=409, detail=str(exc))
    return HTTPException(status_code=502, detail=str(exc))


async def _bounded_pcm(request: Request) -> bytes:
    media_type = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    if media_type != "audio/pcm":
        raise HTTPException(
            status_code=415,
            detail="content type must be audio/pcm (8 kHz, mono, signed 16-bit little-endian)",
        )
    declared = request.headers.get("content-length")
    if declared is not None:
        try:
            declared_size = int(declared)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="invalid content length") from exc
        if declared_size > MAX_PCM_BYTES:
            raise HTTPException(status_code=413, detail="audio message exceeds ten seconds")

    payload = bytearray()
    async for chunk in request.stream():
        if len(payload) + len(chunk) > MAX_PCM_BYTES:
            raise HTTPException(status_code=413, detail="audio message exceeds ten seconds")
        payload.extend(chunk)
    if len(payload) < PCM_FRAME_BYTES or len(payload) % PCM_FRAME_BYTES:
        raise HTTPException(
            status_code=422,
            detail="audio must contain complete 20 ms PCM frames",
        )
    return bytes(payload)


@router.post("/messages")
async def create_audio_message(
    request: Request,
    response: Response,
    camera_id: str = Path(pattern=r"^cam_[0-9a-f]{24}$"),
) -> dict[str, object]:
    """Play up to ten seconds of fixed-format PCM through the selected camera driver."""

    pcm16le = await _bounded_pcm(request)
    try:
        result = await asyncio.to_thread(send_audio_message, camera_id, pcm16le)
    except (
        CameraNotFound,
        Unsupported,
        ControlNotReady,
        ControlBusy,
        ControlOperationError,
    ) as exc:
        raise _failure(exc) from exc
    if not result.completed:
        raise HTTPException(status_code=502, detail="camera did not complete the audio message")
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"
    return {"id": camera_id, **result.public()}
