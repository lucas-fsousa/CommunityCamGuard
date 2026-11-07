"""Classify transient SQLite lock errors, never errors by their message text."""

import sqlite3


def is_lock_busy(error: BaseException) -> bool:
    """For read-side recovery only; never use this to replay a database write."""
    code = getattr(error, 'sqlite_errorcode', None)
    return (isinstance(error, sqlite3.OperationalError) and type(code) is int
            and code & 0xFF in {sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED})
