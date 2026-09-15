"""Read-only preparation for experimental native PTZ; not a capability grant.

Caller must retain per-camera ownership for preparation/motion/cleanup. With no
legacy exact-unit identity, correlated device reads select the driver's compatible
model profile. Client-supplied model claims never authorize native movement.
"""
from __future__ import annotations

import math
import socket
import time

from ....db.p2p import P2PEnrollment
from ..capability_identity import CapabilityIdentity, normalize_identity
from ..ptz_models import directions_for
from .camera_session import open_camera_session
from .contracts import P2PProbeError, P2PPropertyRead
from .model_session import exchange_model_read
from .ptz_protocol import DIRECTIONS, direction_supported
from .ptz_route import NativePtzRoute


def prepare_ptz_route(enrollment: P2PEnrollment, expected: CapabilityIdentity | None, *,
                      camera_id: str, direction: str, budget: float = 20,
                      reviewed_directions: frozenset[str] | None = None) -> NativePtzRoute:
    """Return an owned socket after three fresh correlated reads; never send PTZ.

    Consume immediately under the same reservation. No retries of preparation,
    direct-media rendezvous, movement, fallback or production-profile mutation.
    """
    if (not camera_id or enrollment.camera_id != camera_id
            or (expected is not None and enrollment.device_id != expected.device_id)
            or direction not in DIRECTIONS):
        raise ValueError("native PTZ requires the reviewed camera and direction")
    if type(budget) not in (int, float) or not math.isfinite(budget) or not 1 <= budget <= 20:
        raise ValueError("native PTZ preparation budget must be 1..20 seconds")
    reviewed = reviewed_directions if reviewed_directions is not None else frozenset({direction})
    if direction not in reviewed or reviewed - DIRECTIONS.keys():
        raise ValueError("PTZ direction is not in the reviewed profile")
    deadline = time.monotonic() + budget
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.bind(("", 0))
        node, target, sequence = open_camera_session(sock, enrollment, min(1.5, budget), deadline)
        if str(target.device_id) != enrollment.device_id or not target.status:
            raise P2PProbeError("native PTZ target is not the reviewed online camera")
        reads = []
        for offset, path in enumerate(("ProConst._productInfo", "ProConst._versionInfo", "ProReadonly.devInfo")):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise P2PProbeError("native PTZ preparation exhausted its time budget")
            result = exchange_model_read(sock, node, target, path, (sequence + offset) & 0xFFFFFFFF,
                                         min(2, remaining), retries=1, deadline=deadline,
                                         require_correlated_response=True)
            if type(result.error_code) is not int or result.error_code != 0:
                raise P2PProbeError("native PTZ correlated preflight read failed")
            reads.append(P2PPropertyRead(enrollment.device_id, path, True, False,
                                         result.transport_acknowledged, 0, result.value))
            if offset == 1:
                identity = normalize_identity(reads[0], reads[1], device_id=enrollment.device_id)
                if identity is None or (expected is not None and identity != expected):
                    raise P2PProbeError("native PTZ identity does not match reviewed evidence")
                if expected is None:
                    reviewed = reviewed & directions_for(identity)
                    if direction not in reviewed:
                        raise P2PProbeError("native PTZ model profile is not supported")
        if time.monotonic() >= deadline:
            raise P2PProbeError("native PTZ preparation exhausted its time budget")
        if not direction_supported(reads[2].value, direction):
            raise P2PProbeError("native PTZ direction lacks current axis evidence")
        return NativePtzRoute(sock, node, access_id=enrollment.access_id, device_id=target.device_id,
                              direction=direction, sequence=(sequence + 3) & 0xFFFFFFFF,
                              allowed_directions=frozenset(
                                  d for d in reviewed if direction_supported(reads[2].value, d)))
    except BaseException:
        sock.close()
        raise
