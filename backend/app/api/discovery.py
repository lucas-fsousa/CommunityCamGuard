"""Gentle LAN discovery and registry reconciliation endpoint."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Request

from ..auth import require_auth
from ..db import registry
from ..discovery import active_scan, scan_lock
from ..recording import recorder
from ..services.camera_runtime import probe_and_store, resync_services
from .camera_presenter import camera_out

router = APIRouter(prefix="/api", tags=["discovery"])
log = logging.getLogger(__name__)


@router.post("/discovery/scan", dependencies=[Depends(require_auth)])
def discovery_scan(request: Request, username: str = "", password: str = "") -> dict:
    """Scan gently, refresh configured camera addresses and return new candidates."""

    before = {cam.camera_id: cam.last_ip for cam in registry.list_cameras()}
    if not scan_lock.acquire(blocking=False):
        raise HTTPException(status_code=409, detail="network discovery already running")
    try:
        hosts = active_scan.scan(username=username, password=password)
    finally:
        scan_lock.release()

    def on_rekey(old: str, new: str) -> None:
        try:
            recorder.rekey_segments(old, new)
        except Exception as exc:
            log.warning("could not migrate recordings %s -> %s error_type=%s", old, new, type(exc).__name__)

    configured, candidates = registry.reconcile(hosts, on_rekey=on_rekey)
    if any(before.get(cam.camera_id) != cam.last_ip for cam in configured):
        resync_services(request)
    for index, camera in enumerate(configured):
        if camera.capabilities or not camera.last_ip:
            continue
        try:
            configured[index] = probe_and_store(camera)
        except Exception as exc:
            log.warning("backfill capability probe failed for %s error_type=%s", camera.camera_id, type(exc).__name__)
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
