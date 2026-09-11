"""Backend-only, expiring exact-unit alarm-resource proofs. Never an HTTP write API."""
from __future__ import annotations

import json
import math
import re
from dataclasses import asdict

from ...camera_identity import valid_camera_id
from ...db import connect
from .capability_identity import CapabilityIdentity
from .capability_resources import ResourceProfile, ResourceSelectionProof
from .p2p.alarm_voice import parse_alarm_voice_option_key

REVISION = 1
_MAX_JSON = 65536
_SCHEMA = """CREATE TABLE IF NOT EXISTS yoosee_alarm_resource_profiles (
    camera_id TEXT PRIMARY KEY, identity TEXT NOT NULL, profile TEXT NOT NULL,
    sources TEXT NOT NULL, reviewed_at REAL NOT NULL, expires_at REAL NOT NULL,
    revision INTEGER NOT NULL
)"""


def _identity(value: CapabilityIdentity) -> str:
    encoded = json.dumps(asdict(value), sort_keys=True, separators=(",", ":"))
    if len(encoded) > 2048:
        raise ValueError("resource profile identity is too large")
    return encoded


def _validate(profile: ResourceProfile, sources: dict[str, str], reviewed_at: float,
              expires_at: float) -> None:
    if (
        not valid_camera_id(profile.camera_id)
        or type(profile.enumeration_verified) is not bool
        or len(profile.selections) > 200
        or (not profile.enumeration_verified and profile.selections)
        or any(type(v) not in (int, float) or not math.isfinite(v)
               for v in (reviewed_at, expires_at))
        or not 0 < reviewed_at < expires_at
    ):
        raise ValueError("invalid resource profile or review validity")
    keys = {item.key for item in profile.selections}
    if len(keys) != len(profile.selections) or any(
        parse_alarm_voice_option_key(item.key) is None
        or not isinstance(item.identity_digest, str)
        or re.fullmatch(r"[0-9a-f]{64}", item.identity_digest) is None
        for item in profile.selections
    ):
        raise ValueError("invalid or ambiguous resource selection proof")
    if not isinstance(sources, dict) or set(sources) != keys | {"enumeration"} or any(
        not isinstance(ref, str)
        or re.fullmatch(r"sha256:[0-9a-f]{64} [^\r\n]{1,240}", ref) is None
        for ref in sources.values()
    ):
        raise ValueError("enumeration and each selection require source digest/locator")


def register(profile: ResourceProfile, *, sources: dict[str, str], reviewed_at: float,
             expires_at: float) -> bool:
    """Replace a complete profile only with a newer review; never merge removed grants.

    Store an empty, enumeration_verified=False profile to revoke. Keep that row even
    after expiry so an older review cannot restore revoked selections. Expiry never
    extends automatically and these records grant nothing without fresh observations.
    """
    _validate(profile, sources, reviewed_at, expires_at)
    payload = json.dumps({
        "enumeration_verified": profile.enumeration_verified,
        "selections": [asdict(item) for item in profile.selections],
    }, sort_keys=True)
    provenance = json.dumps(sources, sort_keys=True)
    if max(len(payload), len(provenance)) > _MAX_JSON:
        raise ValueError("resource profile is too large")
    with connect() as conn:
        conn.execute(_SCHEMA)
        result = conn.execute(
            """INSERT INTO yoosee_alarm_resource_profiles VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(camera_id) DO UPDATE SET identity=excluded.identity,
                profile=excluded.profile, sources=excluded.sources,
                reviewed_at=excluded.reviewed_at, expires_at=excluded.expires_at,
                revision=excluded.revision
            WHERE excluded.reviewed_at > yoosee_alarm_resource_profiles.reviewed_at""",
            (profile.camera_id, _identity(profile.identity), payload, provenance,
             reviewed_at, expires_at, REVISION),
        )
        return result.rowcount == 1


def load(*, camera_id: str, identity: CapabilityIdentity, now: float) -> ResourceProfile | None:
    """Load only a current, exact-identity, fully revalidated backend review."""
    if not valid_camera_id(camera_id) or type(now) not in (int, float) or not math.isfinite(now):
        return None
    with connect() as conn:
        conn.execute(_SCHEMA)
        row = conn.execute(
            """SELECT profile, sources, reviewed_at, expires_at FROM yoosee_alarm_resource_profiles
            WHERE camera_id=? AND identity=? AND revision=? AND reviewed_at<=? AND expires_at>?
                AND length(profile)<=? AND length(sources)<=?""",
            (camera_id, _identity(identity), REVISION, now, now, _MAX_JSON, _MAX_JSON),
        ).fetchone()
    if row is None:
        return None
    try:
        value = json.loads(row["profile"])
        items = value["selections"]
        if not isinstance(items, list) or len(items) > 200:
            return None
        profile = ResourceProfile(camera_id, identity, value["enumeration_verified"], tuple(
            ResourceSelectionProof(item["key"], item["identity_digest"]) for item in items
        ))
        _validate(profile, json.loads(row["sources"]), row["reviewed_at"], row["expires_at"])
        return profile
    except (KeyError, TypeError, ValueError, RecursionError):
        return None
