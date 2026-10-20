"""Opt-in exact-unit audio proofs, separated by operation and transport route.

Connection coordinates and enrollment remain prerequisites, never certification.
No profiles are registered or migrations activated by this module.
"""
from __future__ import annotations

import time

from ...db import p2p
from ...db.registry import Camera
from ..base import Unsupported
from ..contracts import ControlNotReady
from . import capability_profiles as profiles
from . import capability_rollout as rollout
from . import capability_snapshot_store as snapshots
from . import rtsp_backchannel

MESSAGE = "audio_message"
STREAM = "audio_stream"
OPERATIONS = frozenset({MESSAGE, STREAM})
RTSP = "rtsp_pcm"
P2P = "p2p_amr"


def unavailable_audio(camera: Camera, operation: str) -> None:
    """Explain denial without camera I/O, refreshing, granting or replaying audio."""
    selected = rollout.selected(camera.camera_id) if camera.camera_id else None
    if operation in OPERATIONS and selected is not None and operation in selected[1]:
        identity = selected[0]
        profile = profiles.load(camera_id=camera.camera_id, identity=identity, now=time.time())
        if profile and profile.camera_id == camera.camera_id and profile.identity == identity:
            proofs = {proof.key: proof for proof in profile.operations}
            proof = proofs.get(operation)
            if (len(proofs) == len(profile.operations) and proof is not None
                    and proof.writable is True and {RTSP, P2P}.intersection(proof.options)):
                raise ControlNotReady("validated camera audio is temporarily unavailable")
    raise Unsupported(operation)


def allowed_routes(camera: Camera, operation: str) -> frozenset[str] | None:
    """None means legacy; an empty set means managed but not currently authorized.

Migrating either audio operation opts the entire audio surface out of legacy
grants. A message proof cannot authorize hold-to-speak or a different transport.
Fresh identity plus a trusted writable route proof is required for each operation.
"""
    if operation not in OPERATIONS:
        raise ValueError("unknown audio operation")
    selected = rollout.selected(camera.camera_id) if camera.camera_id else None
    if selected is None or not OPERATIONS.intersection(selected[1]):
        return None
    identity, operations = selected
    if operation not in operations:
        return frozenset()
    enrollment = p2p.get_enrollment_for_camera(camera.camera_id)
    if enrollment is None or enrollment.device_id != identity.device_id:
        return frozenset()
    now = time.time()
    if not snapshots.is_current(camera_id=camera.camera_id, identity=identity, now=now):
        from .capability_runtime import request_refresh

        request_refresh(camera.camera_id, identity.device_id)
        return frozenset()
    profile = profiles.load(camera_id=camera.camera_id, identity=identity, now=now)
    if profile is None or profile.camera_id != camera.camera_id or profile.identity != identity:
        return frozenset()
    proofs = {proof.key: proof for proof in profile.operations}
    if len(proofs) != len(profile.operations):
        return frozenset()
    proof = proofs.get(operation)
    if proof is None or proof.writable is not True:
        return frozenset()
    available = {P2P}
    if rtsp_backchannel.supported(camera):
        available.add(RTSP)
    return frozenset(available.intersection(proof.options))
