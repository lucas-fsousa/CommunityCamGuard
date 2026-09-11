"""Explain missing managed controls without advertising or authorizing them."""
import time

from ...db import p2p
from ...db.registry import Camera
from ..base import Unsupported
from ..contracts import ControlNotReady
from . import capability_rollout as rollout
from . import capability_snapshot_store as snapshots
from .capability_evidence import EvidenceState


def unavailable_control(camera: Camera, key: str) -> None:
    selected = rollout.selected(camera.camera_id) if camera.camera_id else None
    if selected is not None and key in selected[1]:
        identity = selected[0]
        enrollment = p2p.get_enrollment_for_camera(camera.camera_id)
        if enrollment is not None and enrollment.device_id == identity.device_id:
            state = snapshots.resolve(camera_id=camera.camera_id, identity=identity,
                                      feature=key, now=time.time())
            if state == EvidenceState.UNKNOWN:
                raise ControlNotReady("camera capability evidence is temporarily unavailable; retry later")
    raise Unsupported(key)
