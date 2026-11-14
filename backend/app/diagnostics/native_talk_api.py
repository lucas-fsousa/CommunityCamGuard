"""Explicit, primary/loopback-only, single-use speaker review on a pinned camera."""

import asyncio
import re
import threading
from dataclasses import asdict

from fastapi import APIRouter, Depends, HTTPException, Request

from ..api.intercom import _bounded_pcm
from ..auth import require_primary_session
from ..config import get_settings
from ..db import p2p, registry
from ..drivers import for_camera
from ..drivers.contracts import ControlNotReady, ControlOperationError
from ..drivers.yoosee.shared_talk import send_candidate
from ..services.camera_controls import ControlBusy, _exclusive
from .local_operator import require_local_operator

router = APIRouter(prefix="/api/internal/diagnostics", include_in_schema=False)
_guard = threading.Lock()


def _send(camera_id: str, pcm: bytes) -> dict:
    with _exclusive(camera_id):
        camera = registry.get_camera_by_id(camera_id)
        entry = p2p.get_enrollment_for_camera(camera_id)
        if camera is None or entry is None or for_camera(camera).key != "yoosee":
            raise ValueError("reviewed Yoosee target is unavailable")
        return asdict(send_candidate(entry, pcm))


@router.post("/native-talk", dependencies=[Depends(require_local_operator), Depends(require_primary_session)])
async def native_talk(request: Request) -> dict:
    settings = get_settings()
    if not settings.native_talk_diagnostic_enabled:
        raise HTTPException(404, "diagnostic is disabled")
    camera_id = settings.native_talk_diagnostic_camera_id
    if not re.fullmatch(r"cam_[0-9a-f]{24}", camera_id):
        raise HTTPException(409, "diagnostic requires a pinned server-side camera")
    if request.query_params:
        raise HTTPException(400, "diagnostic accepts no target or query parameters")
    pcm = await _bounded_pcm(request)
    with _guard:
        if getattr(request.app.state, "native_talk_diagnostic_used", False):
            raise HTTPException(409, "diagnostic already attempted in this server process")
        request.app.state.native_talk_diagnostic_used = True
    try:
        result = await asyncio.to_thread(_send, camera_id, pcm)
    except (ControlNotReady, ControlBusy, ValueError):
        raise HTTPException(409, "native talk review unavailable; attempt consumed") from None
    except (ControlOperationError, RuntimeError, OSError):
        raise HTTPException(502, "native talk review failed; attempt consumed") from None
    return {"camera_id": camera_id, **result, "speaker_verified": False}
