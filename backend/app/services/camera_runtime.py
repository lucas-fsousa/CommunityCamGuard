"""Shared camera lookup and live-service reconciliation operations."""

from __future__ import annotations

import logging
from typing import Any

from ..camera_identity import valid_camera_id
from ..db import registry

log = logging.getLogger(__name__)


def resolve_camera(reference: str) -> registry.Camera | None:
    """Resolve an opaque public ID with a bounded legacy-MAC fallback."""

    if valid_camera_id(reference):
        return registry.get_camera_by_id(reference)
    return registry.get_camera(reference)


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
