"""Recording archive and playback HTTP endpoints."""

from __future__ import annotations

import re
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse

from ..auth import require_auth
from ..camera_identity import valid_camera_id
from ..config import get_settings
from ..db import registry
from ..recording import playback, recorder

router = APIRouter(prefix="/api", tags=["recordings"])


def _legacy_recording_mac(value: str) -> str:
    return str(value).replace(":", "").replace("-", "").lower()


def _recording_target(path: str) -> tuple[Path, Path]:
    """Resolve a recording path and keep every endpoint inside its configured root."""
    root = Path(get_settings().recordings_dir).resolve()
    target = Path(path).resolve()
    if root not in target.parents or not target.is_file():
        raise HTTPException(status_code=404, detail="not found")
    return root, target


@router.get("/recordings", dependencies=[Depends(require_auth)])
def recordings(
    camera_id: str | None = None,
    mac: str | None = None,
    day_from: str | None = None,
    day_to: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> dict:
    """Return the paginated recording timeline and its retention policy."""
    if camera_id and not valid_camera_id(camera_id):
        raise HTTPException(status_code=422, detail="invalid camera_id")
    result = recorder.query_segments(
        camera_id=camera_id,
        mac=mac,
        day_from=day_from,
        day_to=day_to,
        limit=limit,
        offset=offset,
    )
    cameras = registry.list_cameras()
    names_by_id = {camera.camera_id: camera.name or camera.mac for camera in cameras}
    names_by_mac = {
        _legacy_recording_mac(camera.mac): camera.name or camera.mac for camera in cameras
    }
    for item in result["items"]:
        item_id = str(item.get("camera_id") or "")
        legacy_mac = _legacy_recording_mac(str(item.get("mac") or ""))
        item["camera_name"] = (
            names_by_id.get(item_id) or names_by_mac.get(legacy_mac) or item.get("mac") or "camera"
        )
    result["retention_days"] = get_settings().recording_retention_days
    return result


@router.get("/recordings/file", dependencies=[Depends(require_auth)])
def recording_file(path: str):
    """Serve a browser-playable segment after validating its archive path."""
    _root, target = _recording_target(path)
    playable = playback.cached_path(target)
    if playable is not None:
        return FileResponse(playable, media_type="video/mp4")
    if not playback.needs_transcode(target):
        return FileResponse(target, media_type="video/mp4")
    playback.prepare_transcode(target)
    raise HTTPException(
        status_code=409,
        detail="seekable playback is still being prepared",
    )


def _recording_playback_state(target: Path) -> dict:
    if playback.cached_path(target) is not None:
        return {"ready": True, "cached": True, "transcoding": False}
    if playback.transcode_in_progress(target):
        return {"ready": False, "cached": False, "transcoding": True}
    browser_playable = not playback.needs_transcode(target)
    return {"ready": browser_playable, "cached": False, "transcoding": False}


@router.post("/recordings/prepare", dependencies=[Depends(require_auth)])
def prepare_recording_playback(path: str) -> dict:
    """Start one shared background HEVC-to-H.264 preparation job."""
    _root, target = _recording_target(path)
    state = _recording_playback_state(target)
    if not state["ready"] and not state["transcoding"]:
        playback.prepare_transcode(target)
        state = _recording_playback_state(target)
    return state


@router.get("/recordings/playback-status", dependencies=[Depends(require_auth)])
def recording_playback_status(path: str) -> dict:
    """Tell the player when the complete seekable artifact is ready."""
    _root, target = _recording_target(path)
    return _recording_playback_state(target)


def _recording_download_name(target: Path, root: Path) -> str:
    """Build ``Camera_Name_<original UTC timestamp>.mp4`` from trusted data."""
    relative = target.relative_to(root)
    directory_key = relative.parts[0] if relative.parts else ""
    camera_id = recorder.segment_camera_id(str(target))
    if not camera_id and valid_camera_id(directory_key):
        camera_id = directory_key
    camera = registry.get_camera_by_id(camera_id) if camera_id else None
    if camera is None:
        camera = next(
            (
                item
                for item in registry.list_cameras()
                if _legacy_recording_mac(item.mac) == directory_key
            ),
            None,
        )
    label = (camera.name if camera and camera.name.strip() else directory_key) or "camera"
    safe_label = re.sub(r"[^\w.-]+", "_", label, flags=re.UNICODE).strip("._")[:80] or "camera"
    return f"{safe_label}_{target.name}"


@router.get("/recordings/download", dependencies=[Depends(require_auth)])
def recording_download(path: str):
    """Download an original segment with a camera-prefixed UTC filename."""
    root, target = _recording_target(path)
    return FileResponse(
        target,
        media_type="video/mp4",
        filename=_recording_download_name(target, root),
        content_disposition_type="attachment",
        headers={"Cache-Control": "private, no-store"},
    )
