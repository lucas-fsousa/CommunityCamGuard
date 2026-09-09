"""Driver-owned persistence of sanitized per-device property observations.

This is evidence, not permission or a certified transport profile. Only backend
collectors may populate it; client-supplied capability dictionaries are not trusted.
"""

from __future__ import annotations

import math
import re

from ...db import connect
from .capability_evidence import EvidenceState

RULE_REVISION = 1
_SCHEMA = """
CREATE TABLE IF NOT EXISTS yoosee_capability_evidence (
    camera_id TEXT NOT NULL,
    device_id TEXT NOT NULL,
    product TEXT NOT NULL,
    firmware TEXT NOT NULL,
    feature TEXT NOT NULL,
    state TEXT NOT NULL,
    observed_at REAL NOT NULL,
    expires_at REAL NOT NULL,
    revision INTEGER NOT NULL,
    PRIMARY KEY (camera_id, feature)
);
"""


def remember(
    *,
    camera_id: str,
    device_id: str,
    product: str,
    firmware: str,
    feature: str,
    state: EvidenceState,
    observed_at: float,
    expires_at: float,
) -> None:
    """Persist one bounded observation, refusing invalid or out-of-order updates."""

    if not re.fullmatch(r"cam_[0-9a-f]{24}", camera_id):
        raise ValueError("invalid capability camera identity")
    if not re.fullmatch(r"\d{6,20}", device_id):
        raise ValueError("invalid capability device identity")
    if not all(isinstance(v, str) and 0 < len(v) <= 128 for v in (product, firmware)):
        raise ValueError("capability product and firmware are required")
    if not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", feature):
        raise ValueError("invalid capability feature")
    if not isinstance(state, EvidenceState):
        raise ValueError("invalid capability evidence state")
    if any(type(v) not in (int, float) or not math.isfinite(v) for v in (observed_at, expires_at)):
        raise ValueError("invalid capability observation time")
    if not 0 <= observed_at < expires_at:
        raise ValueError("invalid capability validity window")
    with connect() as conn:
        conn.execute(_SCHEMA)
        conn.execute(
            """INSERT INTO yoosee_capability_evidence VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(camera_id, feature) DO UPDATE SET
                device_id=excluded.device_id, product=excluded.product,
                firmware=excluded.firmware, state=excluded.state,
                observed_at=excluded.observed_at, expires_at=excluded.expires_at,
                revision=excluded.revision
            WHERE excluded.observed_at > yoosee_capability_evidence.observed_at""",
            (
                camera_id,
                device_id,
                product,
                firmware,
                feature,
                state.value,
                observed_at,
                expires_at,
                RULE_REVISION,
            ),
        )


def resolve(
    *,
    camera_id: str,
    device_id: str,
    product: str,
    firmware: str,
    feature: str,
    now: float,
) -> EvidenceState:
    """Return unknown after identity, firmware, rules, or validity changes."""

    if type(now) not in (int, float) or not math.isfinite(now):
        return EvidenceState.UNKNOWN
    with connect() as conn:
        conn.execute(_SCHEMA)
        row = conn.execute(
            """SELECT state FROM yoosee_capability_evidence
            WHERE camera_id=? AND device_id=? AND product=? AND firmware=? AND feature=?
              AND observed_at<=? AND expires_at>? AND revision=?""",
            (camera_id, device_id, product, firmware, feature, now, now, RULE_REVISION),
        ).fetchone()
    if row is None:
        return EvidenceState.UNKNOWN
    try:
        return EvidenceState(row["state"])
    except ValueError:
        return EvidenceState.UNKNOWN
