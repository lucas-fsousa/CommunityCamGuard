"""Atomic snapshots ordered by database-issued collection tickets, never camera time.

Starting a new collection invalidates the previous snapshot, even if collection
fails. This conservative store supplies evidence only, not control authorization.
"""

from __future__ import annotations

import json
import math
import re
from dataclasses import asdict

from ...db import connect
from .capability_evidence import EvidenceState
from .capability_identity import CapabilityIdentity
from .capability_snapshot import CapabilitySnapshot

RULE_REVISION = 1
_SCHEMA = """CREATE TABLE IF NOT EXISTS yoosee_capability_snapshots (
    camera_id TEXT PRIMARY KEY,
    generation INTEGER NOT NULL,
    identity TEXT,
    evidence TEXT,
    collected_at REAL,
    expires_at REAL,
    revision INTEGER
)"""


def _camera(camera_id: str) -> None:
    if not re.fullmatch(r"cam_[0-9a-f]{24}", camera_id):
        raise ValueError("invalid snapshot camera identity")


def _time(value: float) -> bool:
    return type(value) in (int, float) and math.isfinite(value) and value > 0


def _identity(identity: CapabilityIdentity) -> str:
    return json.dumps(asdict(identity), sort_keys=True, separators=(",", ":"))


def begin(camera_id: str) -> int:
    """Reserve a durable per-camera ticket before network I/O; invalidate old data."""
    _camera(camera_id)
    with connect() as conn:
        conn.execute(_SCHEMA)
        conn.execute(
            """INSERT INTO yoosee_capability_snapshots (camera_id, generation) VALUES (?, 1)
            ON CONFLICT(camera_id) DO UPDATE SET generation=generation+1,
                identity=NULL, evidence=NULL, collected_at=NULL, expires_at=NULL, revision=NULL""",
            (camera_id,),
        )
        row = conn.execute(
            "SELECT generation FROM yoosee_capability_snapshots WHERE camera_id=?", (camera_id,)
        ).fetchone()
        return int(row["generation"])


def save(snapshot: CapabilitySnapshot, *, generation: int, expires_at: float) -> bool:
    """Publish all sanitized evidence and exact identity in one conditional update."""
    _camera(snapshot.camera_id)
    if (
        type(generation) is not int
        or generation <= 0
        or not _time(snapshot.collected_at)
        or not _time(expires_at)
        or expires_at <= snapshot.collected_at
    ):
        raise ValueError("invalid snapshot validity or collection ticket")
    evidence = {}
    for item in snapshot.evidence:
        if (
            not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", item.feature)
            or item.feature in evidence
            or not isinstance(item.state, EvidenceState)
        ):
            raise ValueError("invalid snapshot evidence")
        # Camera timestamps deliberately excluded from persistence/ordering.
        evidence[item.feature] = item.state.value
    with connect() as conn:
        conn.execute(_SCHEMA)
        result = conn.execute(
            """UPDATE yoosee_capability_snapshots SET identity=?, evidence=?,
                collected_at=?, expires_at=?, revision=?
            WHERE camera_id=? AND generation=? AND evidence IS NULL""",
            (
                _identity(snapshot.identity),
                json.dumps(evidence, sort_keys=True),
                snapshot.collected_at,
                expires_at,
                RULE_REVISION,
                snapshot.camera_id,
                generation,
            ),
        )
        return result.rowcount == 1


def resolve(
    *, camera_id: str, identity: CapabilityIdentity, feature: str, now: float
) -> EvidenceState:
    """Resolve only against exact backend identity, valid server time and current rules."""
    if not _time(now):
        return EvidenceState.UNKNOWN
    with connect() as conn:
        conn.execute(_SCHEMA)
        row = conn.execute(
            """SELECT evidence FROM yoosee_capability_snapshots
            WHERE camera_id=? AND identity=? AND collected_at<=? AND expires_at>?
                AND revision=?""",
            (camera_id, _identity(identity), now, now, RULE_REVISION),
        ).fetchone()
    if row is None:
        return EvidenceState.UNKNOWN
    try:
        values = json.loads(row["evidence"])
        value = values.get(feature) if isinstance(values, dict) else None
        return EvidenceState(value) if isinstance(value, str) else EvidenceState.UNKNOWN
    except (ValueError, TypeError):
        return EvidenceState.UNKNOWN
