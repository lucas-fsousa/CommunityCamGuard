"""Nonblocking, demand-driven refresh for explicitly migrated Yoosee units only.

One daemon worker globally, no queue, five-minute attempt backoff per camera. No
camera media or actions; stale evidence stays unknown until the bounded read succeeds.
"""

from __future__ import annotations

import logging
import threading
import time

from ...db import p2p
from .capability_refresh import refresh

log = logging.getLogger(__name__)
_lock = threading.Lock()
_busy = False
_attempts: dict[str, float] = {}
RETRY_SECONDS = 300.0
VALIDITY_SECONDS = 3600.0


def request_refresh(camera_id: str, device_id: str) -> None:
    """Return immediately; callers must already have verified explicit rollout."""
    global _busy
    now = time.monotonic()
    with _lock:
        if _busy or now - _attempts.get(camera_id, -RETRY_SECONDS) < RETRY_SECONDS:
            return
        if len(_attempts) >= 128 and camera_id not in _attempts:
            _attempts.pop(min(_attempts, key=lambda key: _attempts[key]))
        _attempts[camera_id] = now
        _busy = True

    def run():
        global _busy
        try:
            enrollment = p2p.get_enrollment_for_camera(camera_id)
            if enrollment is not None and enrollment.device_id == device_id:
                refresh(enrollment, validity_seconds=VALIDITY_SECONDS)
        except Exception as exc:
            # Never log payloads or exception text that may contain credentials.
            log.warning(
                "capability refresh failed camera=%s error_type=%s", camera_id, type(exc).__name__
            )
        finally:
            with _lock:
                _busy = False

    try:
        threading.Thread(target=run, name="yoosee-capability-read", daemon=True).start()
    except Exception:
        with _lock:
            _busy = False
        raise
