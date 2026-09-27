"""Transactional migration of staged keys; preserve IDs, verifiers and revocations."""

SCHEMA = """CREATE TABLE IF NOT EXISTS dashboard_access_keys (
    id TEXT PRIMARY KEY,
    label TEXT NOT NULL,
    verifier TEXT NOT NULL,
    created_at REAL NOT NULL,
    expires_at REAL CHECK (expires_at IS NULL OR expires_at > created_at),
    revoked_at REAL,
    permissions TEXT NOT NULL
)"""


def ensure(conn) -> None:
    conn.execute(SCHEMA)
    if "permissions" in {row[1] for row in conn.execute("PRAGMA table_info(dashboard_access_keys)")}:
        return
    with conn:
        conn.execute("BEGIN IMMEDIATE")
        # Another connection may have migrated while this one waited for the lock.
        if "permissions" in {row[1] for row in conn.execute("PRAGMA table_info(dashboard_access_keys)")}:
            return
        conn.execute("ALTER TABLE dashboard_access_keys RENAME TO dashboard_access_keys_legacy")
        conn.execute(SCHEMA)
        conn.execute("""INSERT INTO dashboard_access_keys
            SELECT id, label, verifier, created_at, expires_at, revoked_at,
                   '["live","recordings"]' FROM dashboard_access_keys_legacy""")
        conn.execute("DROP TABLE dashboard_access_keys_legacy")
