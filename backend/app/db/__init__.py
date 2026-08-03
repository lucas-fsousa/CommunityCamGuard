"""Shared SQLite access for the app's single database file (``settings.db_path``).

Both the camera registry and the recording index live in this one file, in separate
tables, so they share the connection helper here rather than each re-implementing it.
"""
from __future__ import annotations

import sqlite3

from ..config import get_settings


def connect() -> sqlite3.Connection:
    """Open the app database, creating its parent directory on first use."""
    path = get_settings().db_path
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn
