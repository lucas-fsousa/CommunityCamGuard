"""Disabled-by-default, local single-use identity read diagnostic."""

import re
import threading
from dataclasses import asdict

from fastapi import APIRouter, Depends, HTTPException, Request

from ..auth import require_primary_session
from ..config import get_settings
from ..drivers.contracts import ControlNotReady
from ..drivers.yoosee.p2p.contracts import P2PProbeError
from ..drivers.yoosee.p2p.control_ownership import ControlChannelBusy
from ..services.camera_controls import CameraNotFound, ControlBusy
from .local_operator import require_local_operator
from .yoosee_broker_reads import run_reviewed_broker_reads

router = APIRouter(prefix="/api/internal/diagnostics", include_in_schema=False)
_guard = threading.Lock()


@router.post("/broker-reads", dependencies=[Depends(require_local_operator),
                                          Depends(require_primary_session)])
def broker_reads(request: Request) -> dict:
    settings = get_settings()
    if not settings.broker_read_diagnostic_enabled:
        raise HTTPException(404, "diagnostic is disabled")
    camera_id = settings.broker_read_diagnostic_camera_id
    device_id = settings.broker_read_diagnostic_device_id
    if not re.fullmatch(r"cam_[0-9a-f]{24}", camera_id) or not re.fullmatch(r"[0-9]{1,20}", device_id):
        raise HTTPException(409, "diagnostic requires a reviewed server-side target")
    if request.query_params or request.headers.get("content-length", "0") != "0" or "transfer-encoding" in request.headers:
        raise HTTPException(400, "diagnostic accepts no request parameters or body")
    with _guard:
        if getattr(request.app.state, "broker_read_diagnostic_used", False):
            raise HTTPException(409, "diagnostic already attempted in this server process")
        request.app.state.broker_read_diagnostic_used = True
    try:
        result = run_reviewed_broker_reads(camera_id=camera_id, reviewed_camera_id=camera_id,
                                          reviewed_device_id=device_id,
                                          with_heartbeat=settings.broker_read_diagnostic_heartbeat,
                                          with_worker=settings.broker_read_diagnostic_worker)
    except (ControlBusy, ControlChannelBusy, ControlNotReady, CameraNotFound, ValueError):
        raise HTTPException(409, "diagnostic target is unavailable; attempt consumed") from None
    except (P2PProbeError, OSError):
        raise HTTPException(502, "broker read diagnostic failed; attempt consumed") from None
    return asdict(result)
