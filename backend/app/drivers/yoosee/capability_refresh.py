"""Explicit backend refresh: reserve, collect, atomically publish. Never scheduled here."""

from __future__ import annotations

import math

from ...db.p2p import P2PEnrollment
from . import capability_snapshot_store as store
from .capability_collector import collect_snapshot


def refresh(enrollment: P2PEnrollment, *, validity_seconds: float) -> bool:
    """Caller must choose cache validity; this does not certify source-cache freshness.

    Failed or superseded collection leaves evidence unknown. No camera write/action,
    credential renewal or dashboard integration is introduced by this entry point.
    """
    if (
        type(validity_seconds) not in (int, float)
        or not math.isfinite(validity_seconds)
        or not 0 < validity_seconds <= 86400
    ):
        raise ValueError("snapshot validity must be positive and at most one day")
    generation = store.begin(enrollment.camera_id or "")
    snapshot = collect_snapshot(enrollment)
    if snapshot is None:
        return False
    return store.save(
        snapshot, generation=generation, expires_at=snapshot.collected_at + validity_seconds
    )
