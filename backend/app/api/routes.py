"""REST API for the dashboard.

Endpoints are split into: auth (login/logout, public), and the protected surface —
cameras (CRUD), discovery scan, media/stream info, storage status, and the recording
timeline. Everything but login requires the session cookie (see :mod:`..auth`).

Mutating the camera set (add/delete) reconfigures the live services: go2rtc gets a fresh
config and the recorder is re-synced to the new camera list.
"""
from __future__ import annotations

import logging
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import FileResponse
from pydantic import BaseModel

from .. import drivers
from ..auth import COOKIE_NAME, MAX_AGE, check_key, is_authenticated, issue_token, require_auth
from ..config import get_settings
from ..db import registry
from ..discovery import active_scan, rtsp
from ..media import go2rtc, quality
from ..recording import playback, recorder

router = APIRouter(prefix="/api")
log = logging.getLogger(__name__)


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


class PtzIn(BaseModel):
    direction: str | None = None       # up | down | left | right (not needed for stop)
    action: str = "step"               # "start" (hold) | "stop" (release) | "step" (one nudge)


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


# --- media / storage ----------------------------------------------------------------

@router.get("/media/streams", dependencies=[Depends(require_auth)])
def media_streams(request: Request) -> dict:
    media = getattr(request.app.state, "media", None)
    healthy = bool(media and media.wait_healthy(timeout=1))
    s = get_settings()
    return {"go2rtc_api": s.go2rtc_api, "healthy": healthy,
            # Grid tiles pick their stream from this: at or below it they get the full-resolution
            # feed, above it the cheap substream (see go2rtc.web_stream_id).
            "grid_hd_max_cameras": s.grid_hd_max_cameras,
            # Encoder bitrate level for the transcodes (media/quality.py). Global, config-driven;
            # exposed so the UI can show/inform the current quality. Source selection (substream
            # vs main) is per-camera and driven client-side off has_substream + the stream ids.
            "live_quality": s.live_quality,
            "quality_levels": list(quality.LEVELS),
            "live_hwaccel": s.live_hwaccel}


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
