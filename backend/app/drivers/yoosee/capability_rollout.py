"""Explicit per-unit/per-control migration. No brand-wide rollout or public writes."""

from __future__ import annotations

import json
from dataclasses import asdict

from ...db import connect
from .capability_identity import CapabilityIdentity
from .capability_policy import ValidatedProfile

_SCHEMA = """CREATE TABLE IF NOT EXISTS yoosee_capability_rollout (
    camera_id TEXT PRIMARY KEY, identity TEXT NOT NULL, controls TEXT NOT NULL
)"""


def activate(profile: ValidatedProfile, *, resource_controls: frozenset[str] = frozenset()) -> None:
    """Explicitly opt selected registered proofs into runtime enforcement."""
    if not profile.operations:
        raise ValueError("rollout requires selected operations")
    if resource_controls - {"alarm_voice"}:
        raise ValueError("unknown dynamic resource control")
    with connect() as conn:
        conn.execute(_SCHEMA)
        conn.execute(
            """INSERT INTO yoosee_capability_rollout VALUES (?, ?, ?)
            ON CONFLICT(camera_id) DO UPDATE SET identity=excluded.identity, controls=excluded.controls""",
            (
                profile.camera_id,
                json.dumps(asdict(profile.identity), sort_keys=True),
                json.dumps(sorted({item.key for item in profile.operations} | resource_controls)),
            ),
        )


def selected(camera_id: str) -> tuple[CapabilityIdentity, frozenset[str]] | None:
    with connect() as conn:
        conn.execute(_SCHEMA)
        row = conn.execute(
            "SELECT identity, controls FROM yoosee_capability_rollout WHERE camera_id=?",
            (camera_id,),
        ).fetchone()
    if row is None:
        return None
    # Invalid internal migration data must not silently fall back to legacy grants.
    identity = CapabilityIdentity(**json.loads(row["identity"]))
    return identity, frozenset(json.loads(row["controls"]))
