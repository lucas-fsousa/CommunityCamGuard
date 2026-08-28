"""Live-media status, diagnostics, recovery and authenticated WebSocket proxy."""

from __future__ import annotations

import asyncio
import json
import logging
import urllib.parse
from collections import deque
from datetime import UTC, datetime

import websockets
from fastapi import APIRouter, Depends, HTTPException, Request, WebSocket
from pydantic import BaseModel, Field
from starlette.websockets import WebSocketState

from ..auth import COOKIE_NAME, require_auth, verify_token
from ..camera_identity import valid_camera_id
from ..config import get_settings
from ..media import go2rtc, quality
from ..services.camera_runtime import resolve_camera, resync_services

router = APIRouter(prefix="/api", tags=["media"])
log = logging.getLogger(__name__)
_client_media_events: deque[dict] = deque(maxlen=200)


class MediaClientEventIn(BaseModel):
    """Small, bounded browser snapshot emitted only on live-view state transitions."""

    event: str = Field(min_length=1, max_length=40)
    camera_id: str = Field(default="", max_length=64)
    mac: str = Field(default="", max_length=64)  # deprecated compatibility input
    stream: str = Field(min_length=1, max_length=128)
    metrics: dict[str, bool | int | float | str | None] = Field(default_factory=dict)


@router.get("/media/streams", dependencies=[Depends(require_auth)])
def media_streams(request: Request) -> dict:
    media = getattr(request.app.state, "media", None)
    healthy = bool(media and media.wait_healthy(timeout=1))
    settings = get_settings()
    return {
        "go2rtc_api": settings.go2rtc_api,
        "healthy": healthy,
        "grid_hd_max_cameras": settings.grid_hd_max_cameras,
        "live_quality": settings.live_quality,
        "quality_levels": list(quality.LEVELS),
        "live_hwaccel": settings.live_hwaccel,
    }


@router.get("/media/activity", dependencies=[Depends(require_auth)])
def media_activity(request: Request) -> dict:
    """Return per-stream video-packet liveness for the browser freeze watchdog."""

    media = getattr(request.app.state, "media", None)
    return media.stream_activity() if media else {}


_MEDIA_CLIENT_EVENTS = {
    "waiting",
    "stalled",
    "playing",
    "catchup_start",
    "catchup_end",
    "live_edge_jump",
    "mse_failure",
    "watchdog_recovery",
}


@router.post("/media/client-event", dependencies=[Depends(require_auth)])
def media_client_event(body: MediaClientEventIn, request: Request) -> dict:
    """Correlate a bounded browser transition snapshot with server stream counters."""

    if body.event not in _MEDIA_CLIENT_EVENTS:
        raise HTTPException(status_code=422, detail="unknown media client event")
    if len(body.metrics) > 40:
        raise HTTPException(status_code=413, detail="too many media metrics")
    reference = body.camera_id if valid_camera_id(body.camera_id) else body.mac
    camera = resolve_camera(reference) if reference else None
    if camera is None:
        raise HTTPException(status_code=422, detail="configured camera_id is required")
    event = body.model_dump(exclude={"mac"})
    event["camera_id"] = camera.camera_id
    event["at"] = datetime.now(UTC).isoformat(timespec="milliseconds")
    media = getattr(request.app.state, "media", None)
    try:
        activity = media.stream_activity().get(body.stream) if media else None
    except Exception:  # diagnostics must never interfere with live playback
        activity = None
    if activity is not None:
        event["server"] = activity
    encoded = json.dumps(event, separators=(",", ":"), sort_keys=True)
    if len(encoded) > 8192:
        raise HTTPException(status_code=413, detail="media event too large")
    _client_media_events.append(event)
    log.warning("live_view_event %s", encoded)
    return {"ok": True}


@router.get("/media/client-events", dependencies=[Depends(require_auth)])
def media_client_events() -> list[dict]:
    """Return recent browser transitions, oldest first, from a bounded process-local ring."""

    return list(_client_media_events)


@router.post("/media/recover/{camera_id}", dependencies=[Depends(require_auth)])
def media_recover(camera_id: str, request: Request) -> dict:
    """Restart one camera's local H.264 producer, never its RTSP/recording producer."""

    try:
        camera = resolve_camera(camera_id)
    except (KeyError, ValueError):
        camera = None
    if camera is None:
        raise HTTPException(status_code=404, detail="camera not found")
    media = getattr(request.app.state, "media", None)
    if media is None:
        raise HTTPException(status_code=503, detail="media engine not running")
    if not media.restart_preload(go2rtc.hd_stream_id(camera.camera_id)):
        raise HTTPException(status_code=502, detail="local stream recovery failed")
    return {"ok": True}


@router.websocket("/go2rtc/ws")
async def go2rtc_ws(websocket: WebSocket) -> None:
    """Bridge an authenticated same-origin browser socket to loopback-only go2rtc."""

    if not verify_token(websocket.cookies.get(COOKIE_NAME) or ""):
        await websocket.close(code=1008)
        return
    src = websocket.query_params.get("src", "")
    if not src:
        await websocket.close(code=1008)
        return

    api = get_settings().go2rtc_api.rstrip("/")
    upstream_url = "ws" + api[4:] + "/api/ws?src=" + urllib.parse.quote(src)
    await websocket.accept()
    try:
        async with websockets.connect(upstream_url, open_timeout=5, max_size=None) as upstream:

            async def browser_to_go2rtc() -> None:
                while True:
                    message = await websocket.receive()
                    if message["type"] == "websocket.disconnect":
                        return
                    if message.get("text") is not None:
                        await upstream.send(message["text"])
                    elif message.get("bytes") is not None:
                        await upstream.send(message["bytes"])

            async def go2rtc_to_browser() -> None:
                async for frame in upstream:
                    if isinstance(frame, (bytes, bytearray)):
                        await websocket.send_bytes(bytes(frame))
                    else:
                        await websocket.send_text(frame)

            tasks = [
                asyncio.create_task(browser_to_go2rtc()),
                asyncio.create_task(go2rtc_to_browser()),
            ]
            _, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            for task in pending:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
    except Exception as exc:
        log.debug("go2rtc ws proxy for %s ended: %s", src, exc)
    finally:
        if websocket.application_state != WebSocketState.DISCONNECTED:
            try:
                await websocket.close()
            except Exception:
                pass


@router.post("/media/restart", dependencies=[Depends(require_auth)])
def media_restart(request: Request) -> dict:
    resync_services(request)
    return {"ok": True}
