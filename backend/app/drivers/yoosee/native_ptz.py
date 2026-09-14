"""Finite native PTZ dashboard gestures; no continuous movement or automatic repeat."""
from __future__ import annotations

import logging
import threading
from collections.abc import Callable

from ...db import p2p
from ...db.registry import Camera
from ..contracts import ControlNotReady, ControlOperationError
from .native_ptz_policy import PtzProfile
from .p2p.contracts import P2PProbeError
from .p2p.ptz_motion import PtzBusy, PtzMotion
from .p2p.ptz_prepare import prepare_ptz_route

log = logging.getLogger(__name__)
_guard = threading.Lock()
_active: dict[str, PtzMotion] = {}


def stop(camera_id: str) -> bool:
    with _guard:
        motion = _active.get(camera_id)
        if motion:
            motion.stop()
    # No active native gesture means no cross-transport STOP is needed.
    return True


def step(camera: Camera, direction: str, profile: PtzProfile, fallback: Callable[[], bool]) -> bool:
    motion = PtzMotion(0.2)
    with _guard:
        if camera.camera_id in _active or len(_active) >= 4:
            raise ControlNotReady("another PTZ operation is running; do not queue movements")
        _active[camera.camera_id] = motion
    try:
        entry = p2p.get_enrollment_for_camera(camera.camera_id)
        if entry is None or entry.device_id != profile.identity.device_id:
            raise ControlNotReady("native PTZ enrollment requires review")
        try:
            route = prepare_ptz_route(entry, profile.identity, camera_id=camera.camera_id,
                                      direction=direction, budget=12)
        except P2PProbeError:
            # No START has been constructed/sent by preparation. Only this failure
            # boundary may consider a standard finite-step fallback.
            with _guard:
                if motion.cancelled:
                    return False
            log.info("native_ptz preflight_failed camera=%s fallback=onvif", camera.camera_id)
            return fallback()
        result = motion.run(route)
        log.info("native_ptz camera=%s start_attempted=%s release_confirmed=%s errors=%s",
                 camera.camera_id, result.start_attempted, result.release_delivery_confirmed,
                 ",".join(result.error_types) or "none")
        if result.cancelled and not result.start_attempted:
            return False
        if not result.release_delivery_confirmed or result.error_types:
            raise ControlOperationError("native PTZ outcome is uncertain; movement was not repeated")
        return True
    except (OSError, ValueError, PtzBusy) as exc:
        raise ControlOperationError("native PTZ operation failed; movement was not repeated") from exc
    finally:
        with _guard:
            _active.pop(camera.camera_id, None)
