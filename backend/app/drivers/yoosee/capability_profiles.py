"""Backend-owned, exact-unit homologation records. No public write API or auto-enrollment.

Source references retain a document digest and locator per operation. They are audit
provenance, not cryptographic proof that physical testing occurred. Only the trusted
homologation workflow may register these records; registration does not enable controls.
"""

from __future__ import annotations

import json
import math
import re
from dataclasses import asdict

from ...db import connect
from .capability_identity import CapabilityIdentity
from .capability_policy import OperationProof, ValidatedProfile

REVISION = 1
_SCHEMA = """CREATE TABLE IF NOT EXISTS yoosee_operation_profiles (
    camera_id TEXT PRIMARY KEY, identity TEXT NOT NULL, operations TEXT NOT NULL,
    sources TEXT NOT NULL, reviewed_at REAL NOT NULL, revision INTEGER NOT NULL
)"""


def _identity(identity: CapabilityIdentity) -> str:
    return json.dumps(asdict(identity), sort_keys=True, separators=(",", ":"))


def register(profile: ValidatedProfile, *, sources: dict[str, str], reviewed_at: float) -> bool:
    """Atomically replace a profile only with a later backend review; no merge."""
    if (
        not re.fullmatch(r"cam_[0-9a-f]{24}", profile.camera_id)
        or type(reviewed_at) not in (int, float)
        or not math.isfinite(reviewed_at)
        or reviewed_at <= 0
    ):
        raise ValueError("invalid profile review identity/time")
    operations = []
    keys = set()
    for proof in profile.operations:
        if (
            not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", proof.key)
            or proof.key in keys
            or type(proof.readable) is not bool
            or type(proof.writable) is not bool
            or not (proof.readable or proof.writable)
            or any(not isinstance(v, str) or not 0 < len(v) <= 64 for v in proof.options)
        ):
            raise ValueError("invalid operation proof")
        keys.add(proof.key)
        operations.append(
            {
                "key": proof.key,
                "readable": proof.readable,
                "writable": proof.writable,
                "options": sorted(proof.options),
            }
        )
    if (
        not keys
        or set(sources) != keys
        or any(
            not isinstance(ref, str) or not re.fullmatch(r"sha256:[0-9a-f]{64} [^\r\n]{1,240}", ref)
            for ref in sources.values()
        )
    ):
        raise ValueError("each operation requires bounded source digest and locator")
    with connect() as conn:
        conn.execute(_SCHEMA)
        result = conn.execute(
            """INSERT INTO yoosee_operation_profiles VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(camera_id) DO UPDATE SET identity=excluded.identity,
                operations=excluded.operations, sources=excluded.sources,
                reviewed_at=excluded.reviewed_at, revision=excluded.revision
            WHERE excluded.reviewed_at > yoosee_operation_profiles.reviewed_at""",
            (
                profile.camera_id,
                _identity(profile.identity),
                json.dumps(operations),
                json.dumps(sources, sort_keys=True),
                reviewed_at,
                REVISION,
            ),
        )
        return result.rowcount == 1


def load(*, camera_id: str, identity: CapabilityIdentity, now: float) -> ValidatedProfile | None:
    """Load only exact identity/current rules; future-dated reviews are not trusted."""
    if type(now) not in (int, float) or not math.isfinite(now):
        return None
    with connect() as conn:
        conn.execute(_SCHEMA)
        row = conn.execute(
            """SELECT operations FROM yoosee_operation_profiles
            WHERE camera_id=? AND identity=? AND reviewed_at<=? AND revision=?""",
            (camera_id, _identity(identity), now, REVISION),
        ).fetchone()
    if row is None:
        return None
    try:
        items = json.loads(row["operations"])
        proofs = tuple(
            OperationProof(
                item["key"], item["readable"], item["writable"], frozenset(item["options"])
            )
            for item in items
        )
        return ValidatedProfile(camera_id, identity, proofs)
    except (KeyError, TypeError, ValueError):
        return None
