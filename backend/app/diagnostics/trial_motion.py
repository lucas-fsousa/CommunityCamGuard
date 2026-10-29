"""Explicit operator-only motion scope, never a persistent capability proof."""

from ..db.p2p import P2PEnrollment
from ..drivers.yoosee.p2p.ptz_authority import PtzAuthority, PtzGrant
from ..drivers.yoosee.p2p.ptz_protocol import DIRECTIONS


def motion_grants(entry: P2PEnrollment, directions: frozenset[str]) -> tuple[PtzGrant, ...]:
    if not isinstance(directions, frozenset) or not directions <= DIRECTIONS.keys():
        raise ValueError("explicit trial directions required")
    if not directions:
        return ()
    grants = (PtzGrant(entry.access_id, int(entry.device_id), directions),)
    PtzAuthority(grants)  # validate the same exact-account scope as the real worker
    return grants


def write_allowed(path: str, *, camera_id: str, motion: bool) -> bool:
    # Authentication and CSRF checks still run in the real app routes. This only
    # narrows the experiment; it must never enable another camera or control.
    return path in {"/api/login", "/api/logout", "/api/media/client-event"} or (
        motion and path == f"/api/cameras/{camera_id}/ptz")
