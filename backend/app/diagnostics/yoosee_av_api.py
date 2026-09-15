"""Opt-in, loopback-only, authenticated single-use native AV diagnostic."""

from __future__ import annotations

import ipaddress
import re
import threading
from dataclasses import asdict

from fastapi import APIRouter, Depends, HTTPException, Request

from ..api.local_only import require_local_request
from ..auth import require_auth
from ..config import get_settings
from ..drivers.yoosee.p2p.contracts import P2PProbeError
from ..services.camera_controls import CameraNotFound, ControlBusy
from .yoosee_av import run_reviewed_native_av

router = APIRouter(prefix="/api/internal/diagnostics", include_in_schema=False)
_guard = threading.Lock()


def _local_operator(request: Request) -> None:
    require_auth(request)
    require_local_request(request)
    try:
        peer = ipaddress.ip_address(request.client.host if request.client else "")
    except ValueError:
        raise HTTPException(403, "diagnostic requires direct loopback") from None
    if isinstance(peer, ipaddress.IPv6Address) and peer.ipv4_mapped:
        peer = peer.ipv4_mapped
    forwarded = ("forwarded", "x-forwarded-for", "x-real-ip", "cf-connecting-ip",
                 "true-client-ip", "x-forwarded-host", "x-forwarded-proto")
    if not peer.is_loopback or any(name in request.headers for name in forwarded):
        raise HTTPException(403, "diagnostic requires direct loopback")


@router.post("/native-av", dependencies=[Depends(_local_operator)])
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
                                        reviewed_device_id=device_id)
    except (ControlBusy, CameraNotFound, ValueError):
        raise HTTPException(409, "diagnostic target is unavailable; attempt consumed") from None
    except P2PProbeError:
        raise HTTPException(502, "native AV diagnostic failed; attempt consumed") from None
    return asdict(result)
