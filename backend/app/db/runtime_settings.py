"""Singleton, revisioned non-secret settings overrides in the existing database."""

import json
from contextlib import closing

from . import connect

_SCHEMA = """CREATE TABLE IF NOT EXISTS runtime_settings (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    revision INTEGER NOT NULL,
    overrides TEXT NOT NULL
)"""


class RevisionConflict(Exception):
    pass


def read() -> tuple[int, dict]:
    with closing(connect()) as conn:
        conn.execute(_SCHEMA)
        row = conn.execute("SELECT revision, overrides FROM runtime_settings WHERE id=1").fetchone()
        return (row["revision"], json.loads(row["overrides"])) if row else (0, {})


def write(expected: int, changes: dict) -> tuple[int, dict]:
    """Atomic compare-and-swap; null deletes an override, not the baseline value."""
    with closing(connect()) as conn, conn:
        conn.execute(_SCHEMA)
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT revision, overrides FROM runtime_settings WHERE id=1").fetchone()
        revision, values = (row["revision"], json.loads(row["overrides"])) if row else (0, {})
        if revision != expected:
            raise RevisionConflict()
        for key, value in changes.items():
            if value is None:
                values.pop(key, None)
            else:
                values[key] = value
        revision += 1
        conn.execute("INSERT OR REPLACE INTO runtime_settings VALUES (1, ?, ?)",
                     (revision, json.dumps(values)))
        return revision, values
