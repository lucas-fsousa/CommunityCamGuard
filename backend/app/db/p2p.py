"""Encrypted persistence for proprietary P2P enrollment material.

The vendor bind returns a device subscription token, while the authenticated setup material
contains the terminal access identity used to certify a P2P node.  Both are credentials.  They
must survive an app restart for the native driver to be useful, but must never be returned by the
API or stored as clear columns in SQLite.

This table is keyed by the vendor device ID because it is created before LAN discovery has
necessarily resolved the camera's authoritative MAC.  A later driver association can link the two
identities without rewriting the secret record.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from datetime import UTC, datetime

from . import connect
from .registry import _decrypt, _encrypt

log = logging.getLogger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS p2p_enrollments (
    device_id   TEXT PRIMARY KEY,
    camera_id   TEXT NOT NULL DEFAULT '',
    secret_enc  BLOB NOT NULL,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);
"""

_MIGRATIONS = {
    "camera_id": "ALTER TABLE p2p_enrollments ADD COLUMN camera_id TEXT NOT NULL DEFAULT ''",
}
_CAMERA_ID = re.compile(r"^cam_[0-9a-f]{24}$")


@dataclass(frozen=True, slots=True)
class P2PEnrollment:
    device_id: str
    access_id: int
    access_token: bytes
    dev_token: str | None
    created_at: str
    updated_at: str
    camera_id: str | None = None


def init_db() -> None:
    with connect() as conn:
        conn.executescript(_SCHEMA)
        existing = {row["name"] for row in conn.execute("PRAGMA table_info(p2p_enrollments)")}
        for column, ddl in _MIGRATIONS.items():
            if column not in existing:
                conn.execute(ddl)
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_p2p_enrollments_camera_id "
            "ON p2p_enrollments(camera_id)"
        )
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_p2p_enrollments_camera_id_unique "
            "ON p2p_enrollments(camera_id) WHERE camera_id != ''"
        )


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _validate(
    device_id: str,
    access_id: int,
    access_token: bytes,
    dev_token: str | None,
) -> tuple[str, int, bytes, str | None]:
    normalized_device = str(device_id)
    normalized_access_id = int(access_id)
    normalized_access_token = bytes(access_token)
    normalized_dev_token = None if dev_token is None else str(dev_token)
    if not re.fullmatch(r"\d{6,20}", normalized_device):
        raise ValueError("P2P device ID must contain 6 to 20 digits")
    if not 0 <= normalized_access_id <= 0xFFFFFFFFFFFFFFFF:
        raise ValueError("P2P access ID must fit in an unsigned 64-bit integer")
    if len(normalized_access_token) != 64:
        raise ValueError("P2P access token must contain exactly 64 bytes")
    if normalized_dev_token is not None and re.fullmatch(
        r"[0-9a-fA-F]{128}", normalized_dev_token
    ) is None:
        raise ValueError("P2P device subscription token must contain 128 hexadecimal characters")
    return normalized_device, normalized_access_id, normalized_access_token, normalized_dev_token


def _normalize_camera_id(camera_id: str | None) -> str | None:
    if camera_id is None or not str(camera_id).strip():
        return None
    normalized = str(camera_id).strip()
    if _CAMERA_ID.fullmatch(normalized) is None:
        raise ValueError("camera ID is invalid")
    return normalized


def upsert_enrollment(
    device_id: str,
    *,
    access_id: int,
    access_token: bytes,
    dev_token: str | None = None,
    camera_id: str | None = None,
) -> P2PEnrollment:
    """Validate and atomically persist one enrollment as a single encrypted payload."""
    device_id, access_id, access_token, dev_token = _validate(
        device_id, access_id, access_token, dev_token
    )
    camera_id = _normalize_camera_id(camera_id)
    init_db()
    now = _now()
    with connect() as conn:
        existing = conn.execute(
            "SELECT created_at, camera_id FROM p2p_enrollments WHERE device_id = ?", (device_id,)
        ).fetchone()
        created_at = existing["created_at"] if existing else now
        if camera_id is None and existing and existing["camera_id"]:
            camera_id = existing["camera_id"]
        if camera_id is not None:
            conflict = conn.execute(
                "SELECT device_id FROM p2p_enrollments "
                "WHERE camera_id = ? AND device_id != ?",
                (camera_id, device_id),
            ).fetchone()
            if conflict is not None:
                raise ValueError("camera ID is already linked to another P2P enrollment")
        payload = json.dumps(
            {
                "access_id": str(access_id),
                "access_token": access_token.hex(),
                "dev_token": dev_token,
            },
            separators=(",", ":"),
        )
        conn.execute(
            """
            INSERT INTO p2p_enrollments (device_id, camera_id, secret_enc, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(device_id) DO UPDATE SET
                camera_id=excluded.camera_id,
                secret_enc=excluded.secret_enc,
                updated_at=excluded.updated_at
            """,
            (device_id, camera_id or "", _encrypt(payload), created_at, now),
        )
    return P2PEnrollment(
        device_id, access_id, access_token, dev_token, created_at, now, camera_id
    )


def get_enrollment(device_id: str) -> P2PEnrollment | None:
    init_db()
    with connect() as conn:
        row = conn.execute(
            "SELECT * FROM p2p_enrollments WHERE device_id = ?", (str(device_id),)
        ).fetchone()
    if row is None:
        return None
    try:
        payload = json.loads(_decrypt(row["secret_enc"], label="P2P enrollment"))
        device_id, access_id, access_token, dev_token = _validate(
            row["device_id"],
            int(payload["access_id"]),
            bytes.fromhex(payload["access_token"]),
            payload.get("dev_token"),
        )
    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
        log.warning("stored P2P enrollment is invalid for device=%s", row["device_id"])
        return None
    return P2PEnrollment(
        device_id,
        access_id,
        access_token,
        dev_token,
        row["created_at"],
        row["updated_at"],
        row["camera_id"] or None,
    )


def get_enrollment_for_camera(camera_id: str) -> P2PEnrollment | None:
    """Resolve one exact registered-camera identity without exposing the vendor device ID."""

    normalized = _normalize_camera_id(camera_id)
    if normalized is None:
        return None
    init_db()
    with connect() as conn:
        row = conn.execute(
            "SELECT device_id FROM p2p_enrollments WHERE camera_id = ?", (normalized,)
        ).fetchone()
    return get_enrollment(row["device_id"]) if row else None


def has_enrollment_for_camera(camera_id: str) -> bool:
    """Check an opaque camera association without decrypting P2P credential material."""

    try:
        normalized = _normalize_camera_id(camera_id)
    except ValueError:
        return False
    if normalized is None:
        return False
    init_db()
    with connect() as conn:
        row = conn.execute(
            "SELECT 1 FROM p2p_enrollments WHERE camera_id = ?", (normalized,)
        ).fetchone()
    return row is not None


def link_enrollment_to_camera(device_id: str, camera_id: str) -> P2PEnrollment:
    """Attach a durable enrollment to one public camera identity, enforcing one-to-one use."""

    normalized = _normalize_camera_id(camera_id)
    if normalized is None:
        raise ValueError("camera ID is required")
    init_db()
    with connect() as conn:
        selected = conn.execute(
            "SELECT 1 FROM p2p_enrollments WHERE device_id = ?", (str(device_id),)
        ).fetchone()
        if selected is None:
            raise ValueError("P2P enrollment is unavailable")
        conflict = conn.execute(
            "SELECT device_id FROM p2p_enrollments WHERE camera_id = ? AND device_id != ?",
            (normalized, str(device_id)),
        ).fetchone()
        if conflict is not None:
            raise ValueError("camera ID is already linked to another P2P enrollment")
        conn.execute(
            "UPDATE p2p_enrollments SET camera_id = ?, updated_at = ? WHERE device_id = ?",
            (normalized, _now(), str(device_id)),
        )
    enrollment = get_enrollment(str(device_id))
    if enrollment is None:  # defensive: the row cannot disappear inside this local transaction
        raise ValueError("P2P enrollment is unavailable")
    return enrollment


def has_enrollment(device_id: str) -> bool:
    """Check durable presence without decrypting credential material."""
    init_db()
    with connect() as conn:
        row = conn.execute(
            "SELECT 1 FROM p2p_enrollments WHERE device_id = ?", (str(device_id),)
        ).fetchone()
    return row is not None


def delete_enrollment(device_id: str) -> None:
    init_db()
    with connect() as conn:
        conn.execute("DELETE FROM p2p_enrollments WHERE device_id = ?", (str(device_id),))
