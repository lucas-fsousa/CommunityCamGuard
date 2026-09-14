"""Internal exact-unit PTZ opt-in. No brand defaults or public activation API."""
from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass

from ...db import connect
from .capability_identity import CapabilityIdentity
from .p2p.ptz_protocol import DIRECTIONS

_SCHEMA = """CREATE TABLE IF NOT EXISTS yoosee_native_ptz_rollout
    (camera_id TEXT PRIMARY KEY, identity TEXT NOT NULL, directions TEXT NOT NULL)"""


@dataclass(frozen=True)
class PtzProfile:
    identity: CapabilityIdentity
    directions: frozenset[str]


def activate(camera_id: str, identity: CapabilityIdentity, directions: frozenset[str]) -> None:
    """Operator-reviewed motion/STOP proof only, not capability bits alone."""
    if not re.fullmatch(r"cam_[0-9a-f]{24}", camera_id) or not directions or directions - DIRECTIONS.keys():
        raise ValueError("reviewed camera and PTZ directions required")
    with connect() as conn:
        conn.execute(_SCHEMA)
        conn.execute("INSERT OR REPLACE INTO yoosee_native_ptz_rollout VALUES (?, ?, ?)",
                     (camera_id, json.dumps(asdict(identity)), json.dumps(sorted(directions))))


def selected(camera_id: str) -> PtzProfile | None:
    with connect() as conn:
        conn.execute(_SCHEMA)
        row = conn.execute("SELECT identity, directions FROM yoosee_native_ptz_rollout WHERE camera_id=?",
                           (camera_id,)).fetchone()
    if row is None:
        return None
    identity = CapabilityIdentity(**json.loads(row["identity"]))
    values = json.loads(row["directions"])
    if not isinstance(values, list) or not values or any(value not in DIRECTIONS for value in values):
        raise ValueError("invalid internal native PTZ profile")
    return PtzProfile(identity, frozenset(values))
