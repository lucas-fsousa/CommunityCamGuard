"""Select a separately verified camera on one exclusively owned account channel."""
from __future__ import annotations

import time

from ....db.p2p import P2PEnrollment
from ..capability_identity import CapabilityIdentity
from .contracts import P2PProbeError
from .ptz_prepare import verify_ptz_target
from .ptz_route import NativePtzRoute


def select_target(route: NativePtzRoute, enrollment: P2PEnrollment,
                  expected: CapabilityIdentity | None, *, camera_id: str,
                  direction: str, reviewed: frozenset[str]) -> NativePtzRoute:
    """Transfer socket ownership only after exact target/profile/credential checks."""
    if (route._closed or route._error or (route._started and not route._confirmed)
            or (route._released and not route._confirmed)):
        raise P2PProbeError("cannot select a target on an uncertain PTZ route")
    if (enrollment.camera_id != camera_id or enrollment.access_id != route._access_id
            or not any(k[3:5] == (enrollment.access_id, enrollment.access_token) for k in route._verified)
            or (expected is not None and expected.device_id != enrollment.device_id)
            or direction not in reviewed):
        raise P2PProbeError("native PTZ target binding changed")
    target = route._inventory.get(int(enrollment.device_id))
    if target is None or not target.status:
        raise P2PProbeError("selected P2P camera is not online")
    key = ((camera_id, enrollment.device_id), expected, reviewed, enrollment.access_id,
           enrollment.access_token, enrollment.dev_token)
    allowed = route._verified.get(key)
    if allowed is None:
        sequence = route._receipt_sequence
        route._receipt_sequence = (sequence + 3) & 0xFFFFFFFF
        try:
            allowed = verify_ptz_target(route._sock, route._node, target, enrollment,
                                        expected, direction, reviewed, sequence,
                                        time.monotonic() + 12)
        finally:
            route._sock.setblocking(False)
        # Bounded metadata, no per-model shortcuts. Eviction only means rereading.
        if len(route._verified) >= 16:
            route._verified.pop(next(iter(route._verified)))
        route._verified[key] = allowed
    if direction not in allowed:
        raise P2PProbeError("native PTZ direction lacks current axis evidence")
    fresh = NativePtzRoute(route._sock, route._node, access_id=route._access_id,
                           device_id=target.device_id, direction=direction,
                           sequence=route._receipt_sequence, allowed_directions=allowed)
    fresh._inventory, fresh._verified = route._inventory, route._verified
    route._closed = True
    return fresh
