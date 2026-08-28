"""Gentle LAN discovery and registry reconciliation endpoint."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Request

from ..auth import require_auth
from ..db import registry
from ..discovery import active_scan
from ..recording import recorder
from ..services.camera_runtime import probe_and_store
from .camera_presenter import camera_out

router = APIRouter(prefix="/api", tags=["discovery"])
log = logging.getLogger(__name__)


@router.post("/discovery/scan", dependencies=[Depends(require_auth)])
def discovery_scan(request: Request, username: str = "", password: str = "") -> dict:
    """Scan gently, refresh configured camera addresses and return new candidates."""

    del request  # retained in the HTTP signature for compatibility
    hosts = active_scan.scan(username=username, password=password)

    def on_rekey(old: str, new: str) -> None:
        try:
            recorder.rekey_segments(old, new)
        except Exception as exc:
            log.warning("could not migrate recordings %s -> %s: %s", old, new, exc)

    configured, candidates = registry.reconcile(hosts, on_rekey=on_rekey)
    for index, camera in enumerate(configured):
        if camera.capabilities or not camera.last_ip:
            continue
        try:
            configured[index] = probe_and_store(camera)
        except Exception as exc:
            log.warning("backfill capability probe failed for %s: %s", camera.mac, exc)
    return {
        "configured": [camera_out(camera) for camera in configured],
        "candidates": [
            {
                "mac": candidate.mac,
                "ip": candidate.ip,
                "open_ports": candidate.open_ports,
                "suggested_path": candidate.suggested_path,
                "suggested_username": candidate.suggested_username,
                "vendor": candidate.vendor,
                "model": candidate.model,
                "firmware": candidate.firmware,
                "driver": candidate.driver,
            }
            for candidate in candidates
        ],
    }
