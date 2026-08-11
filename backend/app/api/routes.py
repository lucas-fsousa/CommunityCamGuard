"""REST API for the dashboard.

Endpoints are split into: auth (login/logout, public), and the protected surface —
cameras (CRUD), discovery scan, media/stream info, storage status, and the recording
timeline. Everything but login requires the session cookie (see :mod:`..auth`).

Mutating the camera set (add/delete) reconfigures the live services: go2rtc gets a fresh
config and the recorder is re-synced to the new camera list.
"""
from __future__ import annotations

import asyncio
import json
import logging
import urllib.parse
from collections import deque
from datetime import UTC, datetime
from pathlib import Path

import websockets
from fastapi import APIRouter, Depends, HTTPException, Request, Response, WebSocket
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field, SecretStr
from starlette.websockets import WebSocketState

from .. import drivers
from ..auth import (
    COOKIE_NAME,
    MAX_AGE,
    check_key,
    is_authenticated,
    issue_token,
    require_auth,
    verify_token,
)
from ..config import get_settings
from ..db import registry
from ..discovery import active_scan, rtsp
from ..media import go2rtc, quality
from ..provisioning import (
    LabelError,
    WifiSelectionError,
    inspect_label,
    scan_wifi_networks,
    selected_ssid,
)
from ..recording import playback, recorder
from .local_only import require_local_request

router = APIRouter(prefix="/api")
log = logging.getLogger(__name__)
_client_media_events: deque[dict] = deque(maxlen=200)


# --- schemas -----------------------------------------------------------------------

class LoginIn(BaseModel):
    key: str


class CameraIn(BaseModel):
    mac: str
    name: str | None = None
    username: str | None = None
    password: str | None = None
    stream_path: str | None = None
    rtsp_port: int | None = None
    last_ip: str | None = None
    vendor: str | None = None


class ProvisioningLabelIn(BaseModel):
    """Identity visible on a factory-new camera; none of these fields are credentials."""

    label: str = Field(default="", max_length=512)
    device_id: str = Field(default="", max_length=20)
    capability_code: str = Field(default="", max_length=10)
    firmware_version: str = Field(default="", max_length=64)
    mac: str = Field(default="", max_length=32)


class ProvisioningStartIn(ProvisioningLabelIn):
    """Ephemeral setup request. ``wifi_password`` must never be persisted or logged."""

    wifi_network_id: str = Field(min_length=1, max_length=1024)
    wifi_password: SecretStr = Field(default=SecretStr(""), max_length=128)
    name: str = Field(default="", max_length=100)


class PtzIn(BaseModel):
    direction: str | None = None       # up | down | left | right (not needed for stop)
    action: str = "step"               # "start" (hold) | "stop" (release) | "step" (one nudge)


class MediaClientEventIn(BaseModel):
    """Small, bounded browser snapshot emitted only on live-view state transitions."""

    event: str = Field(min_length=1, max_length=40)
    mac: str = Field(min_length=1, max_length=64)
    stream: str = Field(min_length=1, max_length=128)
    metrics: dict[str, bool | int | float | str | None] = Field(default_factory=dict)


def _camera_out(cam: registry.Camera) -> dict:
    """Registry camera as JSON, without leaking the stored password."""
    return {
        "mac": cam.mac,
        "name": cam.name,
        "username": cam.username,
        "has_password": bool(cam.password),
        "stream_path": cam.stream_path,
        "rtsp_port": cam.rtsp_port,
        "last_ip": cam.last_ip,
        "vendor": cam.vendor,
        "capabilities": cam.capabilities,
        "has_audio": bool(cam.capabilities.get("has_audio")),
        "stream_id": go2rtc.stream_id(cam.mac),
        "web_stream_id": go2rtc.web_stream_id(cam.mac),
        "hd_stream_id": go2rtc.hd_stream_id(cam.mac),
        "has_substream": cam.substream_url is not None,
        # HD and SD are now server-local variants for every camera. This is separate from the
        # vendor camera advertising `/onvif2`, which we intentionally do not open concurrently.
        "has_quality_variants": True,
        "webrtc_url": go2rtc.webrtc_page_url(cam.mac),
        "recording": False,  # live flag filled in by list_cameras()
    }


def _resync(request: Request) -> None:
    """Apply registry changes to the running services (go2rtc + recorder).

    Best-effort: the registry write has already succeeded by the time we get here, so a hiccup
    reconfiguring the live services must not fail the API call (it used to 500 the whole add/
    delete). We log and move on; the next scan/restart reconciles.
    """
    media = getattr(request.app.state, "media", None)
    rec = getattr(request.app.state, "rec", None)
    try:
        if media is not None:
            media.restart()
            media.wait_healthy(timeout=6)
        if rec is not None:
            rec.start()
    except Exception as exc:                       # keep the CRUD op successful regardless
        log.warning("service resync after registry change failed: %s", exc)


# --- auth ---------------------------------------------------------------------------

@router.post("/login")
def login(body: LoginIn, response: Response) -> dict:
    if not check_key(body.key):
        raise HTTPException(status_code=401, detail="Invalid key")
    response.set_cookie(COOKIE_NAME, issue_token(), httponly=True, samesite="lax",
                        max_age=MAX_AGE)
    return {"ok": True}


@router.post("/logout")
def logout(response: Response) -> dict:
    response.delete_cookie(COOKIE_NAME)
    return {"ok": True}


@router.get("/me")
def me(request: Request) -> dict:
    return {"authenticated": is_authenticated(request)}


# --- cameras ------------------------------------------------------------------------

@router.get("/cameras", dependencies=[Depends(require_auth)])
def list_cameras(request: Request) -> list[dict]:
    rec = getattr(request.app.state, "rec", None)
    out = []
    for cam in registry.list_cameras():
        d = _camera_out(cam)
        d["recording"] = bool(rec and rec.is_recording(cam.mac))
        out.append(d)
    return out


def _probe_and_store(cam: registry.Camera) -> registry.Camera:
    """Detect the driver, probe live capabilities (PTZ, audio/video, ports) and persist them."""
    caps = drivers.probe(cam, active_scan.enumerate_ports(cam.last_ip))
    return registry.upsert_camera(cam.mac, capabilities=caps.to_dict())


@router.post("/cameras", dependencies=[Depends(require_auth)])
def upsert_camera(body: CameraIn, request: Request) -> dict:
    # Validate the RTSP credentials before saving: a camera that authenticates now streams later.
    # Reject a wrong password up front instead of storing a camera that can never load (the
    # best-effort capability probe below would otherwise swallow the 401). Only a definitive auth
    # rejection blocks the add — an offline/unreachable camera stays addable and retries later.
    if body.last_ip:
        result = rtsp.check_credentials(
            body.last_ip, body.rtsp_port or registry.DEFAULT_RTSP_PORT,
            body.stream_path or "/onvif1", body.username or "", body.password or "")
        if result == "auth":
            # 422, NOT 401: 401 is reserved for *dashboard session* auth (the frontend redirects to
            # login on any 401). This is a bad *camera* password — a validation error on the body.
            raise HTTPException(status_code=422,
                                detail="camera rejected these credentials (wrong username or password)")
    cam = registry.upsert_camera(
        body.mac, name=body.name, username=body.username, password=body.password,
        stream_path=body.stream_path, rtsp_port=body.rtsp_port, last_ip=body.last_ip,
        vendor=body.vendor,
    )
    # Probe capabilities as part of configuring the camera, so device controls (PTZ, audio, ...)
    # light up immediately without a separate manual "probe" step. Best-effort: a slow/failed
    # probe must not fail the add — the camera is already saved and can be re-probed by hand.
    if cam.last_ip:
        try:
            cam = _probe_and_store(cam)
        except Exception as exc:
            log.warning("capability probe on add failed for %s: %s", cam.mac, exc)
    _resync(request)
    return _camera_out(cam)


@router.delete("/cameras/{mac}", dependencies=[Depends(require_auth)])
def delete_camera(mac: str, request: Request) -> dict:
    registry.delete_camera(mac)
    _resync(request)
    return {"ok": True}


# --- device control / capability probe (routed through the camera's driver) --------

@router.post("/cameras/{mac}/probe", dependencies=[Depends(require_auth)])
def probe_camera(mac: str) -> dict:
    """Detect the camera's driver and probe its live capabilities (PTZ, audio/video, ports)."""
    cam = registry.get_camera(mac)
    if cam is None:
        raise HTTPException(status_code=404, detail="camera not found")
    if not cam.last_ip:
        raise HTTPException(status_code=409, detail="camera has no known IP; run a scan first")
    return _camera_out(_probe_and_store(cam))


@router.post("/cameras/{mac}/ptz", dependencies=[Depends(require_auth)])
def ptz_move(mac: str, body: PtzIn) -> dict:
    """Pan/tilt the camera. ``action``: ``start``/``stop`` for press-and-hold, ``step`` for a nudge."""
    cam = registry.get_camera(mac)
    if cam is None:
        raise HTTPException(status_code=404, detail="camera not found")
    try:
        ok = drivers.for_camera(cam).ptz(cam, body.direction, (body.action or "step").lower())
    except drivers.Unsupported as exc:
        raise HTTPException(status_code=501, detail="this camera doesn't support PTZ") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not ok:
        raise HTTPException(status_code=502, detail="camera did not accept the PTZ command")
    return {"ok": True, "action": (body.action or "step").lower(),
            "direction": (body.direction or "").lower()}


@router.post("/cameras/{mac}/reboot", dependencies=[Depends(require_auth)])
def reboot_camera(mac: str) -> dict:
    """Reboot the camera in software, if its driver supports it (e.g. ONVIF SystemReboot)."""
    cam = registry.get_camera(mac)
    if cam is None:
        raise HTTPException(status_code=404, detail="camera not found")
    try:
        ok = drivers.for_camera(cam).reboot(cam)
    except drivers.Unsupported as exc:
        raise HTTPException(status_code=501, detail="this camera doesn't support software reboot") from exc
    if not ok:
        raise HTTPException(status_code=502, detail="camera did not accept the reboot command")
    return {"ok": True, "rebooting": True}


# --- discovery ----------------------------------------------------------------------

@router.post("/discovery/scan", dependencies=[Depends(require_auth)])
def discovery_scan(request: Request, username: str = "", password: str = "") -> dict:
    """Gentle subnet scan. Returns known cameras (IP refreshed) and new candidates."""
    hosts = active_scan.scan(username=username, password=password)
    rekeyed: list[tuple[str, str]] = []

    def on_rekey(old: str, new: str) -> None:
        # A camera moved to its authoritative ONVIF MAC: carry its recordings across too, or the
        # history would be stranded under the old key (see recorder.rekey_segments).
        rekeyed.append((old, new))
        try:
            recorder.rekey_segments(old, new)
        except Exception as exc:                   # never fail a scan over a housekeeping move
            log.warning("could not migrate recordings %s -> %s: %s", old, new, exc)

    configured, candidates = registry.reconcile(hosts, on_rekey=on_rekey)
    # Cameras added before the probe-on-add change carry no capabilities, so their controls (PTZ,
    # audio) stay dark until someone clicks "probe" by hand. A scan is the natural moment to fill
    # that in: the camera just answered, and this is already the slow, user-initiated path. Best-
    # effort per camera — these cheap cams are probed gently and a failure must not fail the scan.
    for i, cam in enumerate(configured):
        if cam.capabilities or not cam.last_ip:
            continue
        try:
            configured[i] = _probe_and_store(cam)
        except Exception as exc:
            log.warning("backfill capability probe failed for %s: %s", cam.mac, exc)
    if rekeyed:
        _resync(request)   # go2rtc streams and recorder processes are keyed by MAC
    return {
        "configured": [_camera_out(c) for c in configured],
        "candidates": [
            {"mac": c.mac, "ip": c.ip, "open_ports": c.open_ports,
             "suggested_path": c.suggested_path, "suggested_username": c.suggested_username,
             "vendor": c.vendor, "model": c.model, "firmware": c.firmware, "driver": c.driver}
            for c in candidates
        ],
    }


# --- factory-new provisioning (strictly localhost-only) -----------------------------

_LOCAL_PROVISIONING = [Depends(require_auth), Depends(require_local_request)]


def _inspect_provisioning_label(body: ProvisioningLabelIn) -> dict:
    try:
        return inspect_label(
            label=body.label,
            device_id=body.device_id,
            capability_code=body.capability_code,
            firmware_version=body.firmware_version,
            mac=body.mac,
        )
    except LabelError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/provisioning/status", dependencies=_LOCAL_PROVISIONING, tags=["provisioning"])
def provisioning_status() -> dict:
    """Describe the local onboarding surface without probing or changing any camera."""
    return {
        "local_only": True,
        "label_inspection": True,
        "transport_ready": False,
        "transports": {
            "qr": "protocol-recovery",
            "softap": "protocol-recovery",
            "bluetooth": "planned",
            "wired": "planned",
        },
    }


@router.post("/provisioning/inspect", dependencies=_LOCAL_PROVISIONING, tags=["provisioning"])
def provisioning_inspect(body: ProvisioningLabelIn) -> dict:
    """Validate and decode a scanned/typed factory label without contacting the camera."""
    return _inspect_provisioning_label(body)


@router.get("/provisioning/networks", dependencies=_LOCAL_PROVISIONING, tags=["provisioning"])
def provisioning_networks(response: Response) -> dict:
    """Read-only scan from the server's Wi-Fi radio; SSIDs carry short-lived signed IDs."""
    networks, scanner, error = scan_wifi_networks()
    response.headers["Cache-Control"] = "no-store"
    return {
        "networks": [network.public() for network in networks],
        "scanner": scanner,
        "error": error or None,
    }


@router.post("/provisioning/start", dependencies=_LOCAL_PROVISIONING, tags=["provisioning"])
def provisioning_start(body: ProvisioningStartIn) -> dict:
    """Begin onboarding once a transport is available; credentials remain request-local.

    This endpoint intentionally fails closed until the recovered ``AP_NET_CONFIG`` transport is
    implemented.  Keeping the route and schema in place lets the UI/security boundary land without
    ever claiming that a camera was configured when no packet was sent.
    """
    identity = _inspect_provisioning_label(body)
    try:
        selected_ssid(body.wifi_network_id)
    except WifiSelectionError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    # Touch the SecretStr only inside this request. Do not log, return or persist its value.
    body.wifi_password.get_secret_value()
    modes = ", ".join(identity["setup_modes"])
    raise HTTPException(
        status_code=501,
        detail=f"camera label accepted ({modes}); provisioning transport is not ready yet",
    )


# --- media / storage ----------------------------------------------------------------

@router.get("/media/streams", dependencies=[Depends(require_auth)])
def media_streams(request: Request) -> dict:
    media = getattr(request.app.state, "media", None)
    healthy = bool(media and media.wait_healthy(timeout=1))
    s = get_settings()
    return {"go2rtc_api": s.go2rtc_api, "healthy": healthy,
            # Grid tiles in opt-in Auto mode pick their local stream from this: at or below it they
            # get full resolution, above it the locally downscaled variant.
            "grid_hd_max_cameras": s.grid_hd_max_cameras,
            # Encoder bitrate level for the transcodes (media/quality.py). Global, config-driven;
            # exposed so the UI can show/inform the current quality. Variant selection is
            # per-camera and client-side; both stream IDs are served from the local media hub.
            "live_quality": s.live_quality,
            "quality_levels": list(quality.LEVELS),
            "live_hwaccel": s.live_hwaccel}


@router.get("/media/activity", dependencies=[Depends(require_auth)])
def media_activity(request: Request) -> dict:
    """Per-stream video-packet liveness, for the dashboard's freeze watchdog (see go2rtc)."""
    media = getattr(request.app.state, "media", None)
    return media.stream_activity() if media else {}


_MEDIA_CLIENT_EVENTS = {
    "waiting", "stalled", "playing", "catchup_start", "catchup_end",
    "live_edge_jump", "mse_failure", "watchdog_recovery",
}


@router.post("/media/client-event", dependencies=[Depends(require_auth)])
def media_client_event(body: MediaClientEventIn, request: Request) -> dict:
    """Correlate a browser stall/catch-up with the server's stream counters at that instant.

    This deliberately accepts only scalar metrics and a short allow-list of transition names. It
    never receives camera credentials, media data or arbitrary browser logs. Events are rare (not a
    periodic beacon), kept in a 200-entry in-memory ring and also emitted as one structured log line.
    """
    if body.event not in _MEDIA_CLIENT_EVENTS:
        raise HTTPException(status_code=422, detail="unknown media client event")
    if len(body.metrics) > 40:
        raise HTTPException(status_code=413, detail="too many media metrics")
    event = body.model_dump()
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
    """Recent browser transition snapshots, oldest first (process-local, bounded to 200)."""
    return list(_client_media_events)


@router.post("/media/recover/{mac}", dependencies=[Depends(require_auth)])
def media_recover(mac: str, request: Request) -> dict:
    """Restart one camera's local H.264 producer, never its RTSP/recording producer."""
    try:
        cam = registry.get_camera(mac)
    except (KeyError, ValueError):
        cam = None
    if cam is None:
        raise HTTPException(status_code=404, detail="camera not found")
    media = getattr(request.app.state, "media", None)
    if media is None:
        raise HTTPException(status_code=503, detail="media engine not running")
    ok = media.restart_preload(go2rtc.hd_stream_id(cam.mac))
    if not ok:
        raise HTTPException(status_code=502, detail="local stream recovery failed")
    return {"ok": True}


@router.websocket("/go2rtc/ws")
async def go2rtc_ws(websocket: WebSocket) -> None:
    """Authenticated same-origin proxy to go2rtc's stream WebSocket.

    The live player (``frontend/player.js``) is served from the app origin so the dashboard can
    read the real ``<video>`` for the freeze watchdog — but go2rtc rejects a cross-origin WS
    handshake (403 on any foreign ``Origin``). Rather than open go2rtc up with ``api.origin: "*"``
    (go2rtc has no auth and can run ``exec:`` sources — a CSRF/RCE surface reachable through the
    user's tunnel), we bridge here: the browser connects to this same-origin endpoint (carrying the
    session cookie, so only a logged-in dashboard can use it), and we relay to go2rtc **without**
    forwarding the browser ``Origin`` header (go2rtc accepts an origin-less handshake). Bonus: only
    the app port needs to be reachable/tunnelled for signalling — go2rtc stays fully loopback-bound.
    """
    if not verify_token(websocket.cookies.get(COOKIE_NAME) or ""):
        await websocket.close(code=1008)   # policy violation (unauthenticated)
        return
    src = websocket.query_params.get("src", "")
    if not src:
        await websocket.close(code=1008)
        return

    api = get_settings().go2rtc_api.rstrip("/")
    upstream_url = "ws" + api[4:] + "/api/ws?src=" + urllib.parse.quote(src)  # http->ws, no Origin
    await websocket.accept()
    try:
        async with websockets.connect(upstream_url, open_timeout=5, max_size=None) as up:
            async def browser_to_go2rtc() -> None:
                while True:
                    msg = await websocket.receive()
                    if msg["type"] == "websocket.disconnect":
                        return
                    if msg.get("text") is not None:
                        await up.send(msg["text"])
                    elif msg.get("bytes") is not None:
                        await up.send(msg["bytes"])

            async def go2rtc_to_browser() -> None:
                async for frame in up:
                    if isinstance(frame, (bytes, bytearray)):
                        await websocket.send_bytes(bytes(frame))
                    else:
                        await websocket.send_text(frame)

            tasks = [asyncio.create_task(browser_to_go2rtc()), asyncio.create_task(go2rtc_to_browser())]
            _, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            for task in pending:
                task.cancel()
            # Consume both the completed direction and the cancellation. Without this, normal
            # browser disconnects leave "Task exception was never retrieved" warnings and can
            # keep relay resources alive until garbage collection (especially harmful for MSE,
            # where this socket carries the media itself rather than signalling only).
            await asyncio.gather(*tasks, return_exceptions=True)
    except Exception as exc:   # upstream connect/relay failure — just close the browser socket
        log.debug("go2rtc ws proxy for %s ended: %s", src, exc)
    finally:
        # Best-effort close: the browser is usually already gone (that is what ended the relay),
        # so closing can itself raise ClientDisconnected/WebSocketDisconnect — which is not an error.
        if websocket.application_state != WebSocketState.DISCONNECTED:
            try:
                await websocket.close()
            except Exception:
                pass


@router.post("/media/restart", dependencies=[Depends(require_auth)])
def media_restart(request: Request) -> dict:
    _resync(request)
    return {"ok": True}


@router.get("/storage", dependencies=[Depends(require_auth)])
def storage_status(request: Request) -> dict:
    monitor = getattr(request.app.state, "storage", None)
    if monitor is None:
        raise HTTPException(status_code=503, detail="storage monitor not running")
    st = monitor.state()
    return st.__dict__


# --- recordings ---------------------------------------------------------------------

@router.get("/recordings", dependencies=[Depends(require_auth)])
def recordings(mac: str | None = None, day_from: str | None = None,
               day_to: str | None = None, limit: int = 50, offset: int = 0) -> dict:
    """Paginated recordings query — returns {items, total, limit, offset, retention_days}.

    ``retention_days`` is page context (0 = kept forever) so the browser can tell the user how
    long footage is kept before the retention job deletes it (see docs/DECISIONS.md §22).
    """
    res = recorder.query_segments(mac=mac, day_from=day_from, day_to=day_to,
                                  limit=limit, offset=offset)
    res["retention_days"] = get_settings().recording_retention_days
    return res


@router.get("/recordings/file", dependencies=[Depends(require_auth)])
def recording_file(path: str):
    """Serve one segment for playback/download, guarded to the recordings root.

    Segments are HEVC, which browsers can't decode in a ``<video>`` tag, so HEVC segments are
    transcoded to H.264 for playback: a cached copy (seekable) if we have one, else a progressive
    stream that also warms the cache (see :mod:`..recording.playback`). H.264 segments are served
    directly.
    """
    root = Path(get_settings().recordings_dir).resolve()
    target = Path(path).resolve()
    if root not in target.parents or not target.is_file():
        raise HTTPException(status_code=404, detail="not found")
    # HEVC -> serve a cached H.264 transcode (seekable, correct duration); H.264 -> serve as-is.
    playable = playback.transcoded_path(target)
    return FileResponse(playable or target, media_type="video/mp4")
