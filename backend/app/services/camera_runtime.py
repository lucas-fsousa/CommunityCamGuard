"""Shared camera lookup and live-service reconciliation operations."""

from __future__ import annotations

import logging
from typing import Any

from .. import drivers
from ..camera_identity import valid_camera_id
from ..db import registry
from ..discovery import active_scan
from ..media import go2rtc

log = logging.getLogger(__name__)


def resolve_camera(reference: str) -> registry.Camera | None:
    """Resolve an opaque public ID with a bounded legacy-MAC fallback."""

    if valid_camera_id(reference):
        return registry.get_camera_by_id(reference)
    return registry.get_camera(reference)


def probe_and_store(camera: registry.Camera) -> registry.Camera:
    """Probe through the selected driver and persist the capability snapshot."""

    capabilities = drivers.probe(camera, active_scan.enumerate_ports(camera.last_ip))
    return registry.upsert_camera(
        camera.mac,
        camera_id=camera.camera_id,
        capabilities=capabilities.to_dict(),
    )


def runtime_statuses(request: Any, cameras: list[registry.Camera]) -> list[dict]:
    """Read media activity once and map it to configured camera identities."""

    media = getattr(request.app.state, "media", None)
    recorder = getattr(request.app.state, "rec", None)
    try:
        online_probe = getattr(media, "stream_online", None)
        online_streams = online_probe() if callable(online_probe) else {}
    except (OSError, ValueError):
        online_streams = {}
    return [
        {
            "id": camera.camera_id,
            "mac": camera.mac,
            "online": bool(online_streams.get(go2rtc.stream_id(camera.camera_id), False)),
            "recording": bool(recorder and recorder.is_recording(camera.camera_id)),
        }
        for camera in cameras
    ]


def resync_services(request: Any) -> None:
    """Best-effort reconciliation of media and recorder state after registry changes."""

    media = getattr(request.app.state, "media", None)
    recorder = getattr(request.app.state, "rec", None)
    try:
        if media is not None:
            media.restart()
            media.wait_healthy(timeout=6)
        if recorder is not None:
            recorder.start()
    except Exception as exc:  # registry changes are durable; startup reconciles later
        log.warning("service resync after registry change failed: %s", exc)
