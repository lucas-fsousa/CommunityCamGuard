"""Opt-in, loopback-only, authenticated single-use native AV diagnostic."""

from __future__ import annotations

import re
import threading
from dataclasses import asdict

from fastapi import APIRouter, Depends, HTTPException, Request

from ..config import get_settings
from ..drivers.yoosee.p2p.av_route import AvBootstrapError
from ..drivers.yoosee.p2p.contracts import P2PProbeError
from ..drivers.yoosee.p2p.control_ownership import ControlChannelBusy
from ..services.camera_controls import CameraNotFound, ControlBusy
from .local_operator import require_local_operator
from .yoosee_av import run_reviewed_native_av

router = APIRouter(prefix="/api/internal/diagnostics", include_in_schema=False)
_guard = threading.Lock()


@router.post("/native-av", dependencies=[Depends(require_local_operator)])
def native_av(request: Request) -> dict:
    settings = get_settings()
    if not settings.native_av_diagnostic_enabled:
        raise HTTPException(404, "diagnostic is disabled")
    camera_id = settings.native_av_diagnostic_camera_id
    device_id = settings.native_av_diagnostic_device_id
    if not re.fullmatch(r"cam_[0-9a-f]{24}", camera_id) or not re.fullmatch(r"[0-9]{1,20}", device_id):
        raise HTTPException(409, "diagnostic requires a reviewed server-side target")
    # No request-provided target, duration, URL, credentials or protocol fields.
    if request.query_params or request.headers.get("content-length", "0") != "0" or "transfer-encoding" in request.headers:
        raise HTTPException(400, "diagnostic accepts no request parameters or body")
    with _guard:
        if getattr(request.app.state, "native_av_diagnostic_used", False):
            raise HTTPException(409, "diagnostic already attempted in this server process")
        request.app.state.native_av_diagnostic_used = True
    try:
        result = run_reviewed_native_av(camera_id=camera_id, reviewed_camera_id=camera_id,
                                        reviewed_device_id=device_id,
                                        platform_only=settings.native_av_diagnostic_platform_only,
                                        tcp_relay=settings.native_av_diagnostic_tcp_relay,
                                        tcp_profile=settings.native_av_diagnostic_tcp_profile,
                                        decode_video=settings.native_av_diagnostic_decode_video)
    except (ControlBusy, ControlChannelBusy, CameraNotFound, ValueError):
        raise HTTPException(409, "diagnostic target is unavailable; attempt consumed") from None
    except AvBootstrapError as exc:
        raise HTTPException(502, dict(message="native AV bootstrap failed; attempt consumed",
                                     **exc.observations)) from None
    except (P2PProbeError, OSError):
        raise HTTPException(502, "native AV diagnostic failed; attempt consumed") from None
    return asdict(result)
