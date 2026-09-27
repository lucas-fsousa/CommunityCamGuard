"""Persistent temporary-key records; no plaintext credentials or session cookies.

No callers should authenticate against this repository directly: the service owns
credential parsing, constant-time verification and current-time validity checks.
"""

import json
from contextlib import closing

from . import connect
from .access_key_schema import ensure

_PUBLIC = "id, label, created_at, expires_at, revoked_at, permissions"


def _record(row) -> dict:
    result = dict(row)
    result["permissions"] = json.loads(result["permissions"])
    return result


def insert(key_id: str, label: str, verifier: str, created_at: float, expires_at: float | None,
           permissions: tuple[str, ...] = ("live", "recordings")) -> None:
    with closing(connect()) as conn, conn:
        ensure(conn)
        conn.execute(
            "INSERT INTO dashboard_access_keys VALUES (?, ?, ?, ?, ?, NULL, ?)",
            (key_id, label, verifier, created_at, expires_at, json.dumps(permissions)),
        )


def get(key_id: str, *, with_verifier: bool = False) -> dict | None:
    columns = _PUBLIC + (", verifier" if with_verifier else "")
    with closing(connect()) as conn:
        ensure(conn)
        row = conn.execute(f"SELECT {columns} FROM dashboard_access_keys WHERE id=?", (key_id,)).fetchone()
        return _record(row) if row else None


def page(limit: int, offset: int) -> list[dict]:
    with closing(connect()) as conn:
        ensure(conn)
        rows = conn.execute(
            f"SELECT {_PUBLIC} FROM dashboard_access_keys ORDER BY created_at DESC, id LIMIT ? OFFSET ?",
            (limit, offset),
        ).fetchall()
        return [_record(row) for row in rows]


def revoke(key_id: str, now: float) -> dict | None:
    """Idempotent atomic transition. Preserve the first revocation timestamp."""
    with closing(connect()) as conn, conn:
        ensure(conn)
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(
            "UPDATE dashboard_access_keys SET revoked_at=COALESCE(revoked_at, ?) WHERE id=?",
            (now, key_id),
        )
        row = conn.execute(f"SELECT {_PUBLIC} FROM dashboard_access_keys WHERE id=?", (key_id,)).fetchone()
        return _record(row) if row else None
