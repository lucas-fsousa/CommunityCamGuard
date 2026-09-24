"""Persistent temporary-key records; no plaintext credentials or session cookies.

No callers should authenticate against this repository directly: the service owns
credential parsing, constant-time verification and current-time validity checks.
"""

from contextlib import closing

from . import connect

_SCHEMA = """CREATE TABLE IF NOT EXISTS dashboard_access_keys (
    id TEXT PRIMARY KEY,
    label TEXT NOT NULL,
    verifier TEXT NOT NULL,
    created_at REAL NOT NULL,
    expires_at REAL NOT NULL CHECK (expires_at > created_at),
    revoked_at REAL
)"""
_PUBLIC = "id, label, created_at, expires_at, revoked_at"


def insert(key_id: str, label: str, verifier: str, created_at: float, expires_at: float) -> None:
    with closing(connect()) as conn, conn:
        conn.execute(_SCHEMA)
        conn.execute(
            "INSERT INTO dashboard_access_keys VALUES (?, ?, ?, ?, ?, NULL)",
            (key_id, label, verifier, created_at, expires_at),
        )


def get(key_id: str, *, with_verifier: bool = False) -> dict | None:
    columns = _PUBLIC + (", verifier" if with_verifier else "")
    with closing(connect()) as conn:
        conn.execute(_SCHEMA)
        row = conn.execute(f"SELECT {columns} FROM dashboard_access_keys WHERE id=?", (key_id,)).fetchone()
        return dict(row) if row else None


def page(limit: int, offset: int) -> list[dict]:
    with closing(connect()) as conn:
        conn.execute(_SCHEMA)
        rows = conn.execute(
            f"SELECT {_PUBLIC} FROM dashboard_access_keys ORDER BY created_at DESC, id LIMIT ? OFFSET ?",
            (limit, offset),
        ).fetchall()
        return [dict(row) for row in rows]


def revoke(key_id: str, now: float) -> dict | None:
    """Idempotent atomic transition. Preserve the first revocation timestamp."""
    with closing(connect()) as conn, conn:
        conn.execute(_SCHEMA)
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(
            "UPDATE dashboard_access_keys SET revoked_at=COALESCE(revoked_at, ?) WHERE id=?",
            (now, key_id),
        )
        row = conn.execute(f"SELECT {_PUBLIC} FROM dashboard_access_keys WHERE id=?", (key_id,)).fetchone()
        return dict(row) if row else None
