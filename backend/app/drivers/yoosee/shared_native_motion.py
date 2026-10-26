"""Bounded dashboard PTZ on an explicitly granted existing native account."""

import time

from ...db import p2p
from ...db.registry import Camera
from ..contracts import ControlNotReady, ControlOperationError
from .native_ptz_policy import PtzProfile
from .p2p.ptz_motion import PtzMotion
from .shared_control_reads import has_session, session_for


def step(camera: Camera, direction: str, profile: PtzProfile, motion: PtzMotion) -> bool | None:
    """None only without a native owner; busy/failed shared operations never fall back."""
    if not has_session(camera.camera_id):
        return None
    entry = p2p.get_enrollment_for_camera(camera.camera_id)
    if (entry is None or direction not in profile.directions
            or (profile.identity is not None and profile.identity.device_id != entry.device_id)):
        raise ControlNotReady("native PTZ enrollment or direction requires review")
    session = session_for(entry)
    if session is None:
        raise ControlNotReady("native PTZ owner changed; gesture was not repeated")
    if motion.cancelled:
        return False
    try:
        ticket = session.submit_motion(direction)
    except ValueError:
        raise ControlNotReady("native media owner has no ready reviewed PTZ grant") from None
    try:
        deadline = time.monotonic() + 4
        while time.monotonic() < deadline:
            if motion.cancelled:
                session.cancel_motion(ticket)
            result = session.take_motion(ticket)
            if result is not None:
                if not result.start_attempted:
                    return False
                if not result.release_delivery_confirmed or result.send_failed:
                    raise ControlOperationError("native PTZ outcome is uncertain; movement was not repeated")
                return True
            time.sleep(.005)
        raise ControlOperationError("native PTZ timed out; movement was not repeated")
    finally:
        # The socket owner still drains STOP, even when the HTTP caller is gone.
        # Unknown STOP keeps the inbox poisoned; only safe completion frees it.
        session.abandon_motion(ticket)
