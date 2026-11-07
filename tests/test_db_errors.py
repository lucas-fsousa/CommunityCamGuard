import sqlite3

import pytest

from backend.app.db.errors import is_lock_busy


@pytest.mark.parametrize('code,expected', [(sqlite3.SQLITE_BUSY, True),
    (sqlite3.SQLITE_LOCKED, True), (sqlite3.SQLITE_BUSY_SNAPSHOT, True),
    (sqlite3.SQLITE_IOERR, False), (sqlite3.SQLITE_CORRUPT, False)])
def test_only_sqlite_lock_codes_are_transient(code, expected):
    error = sqlite3.OperationalError('PRIVATE_DETAIL')
    error.sqlite_errorcode = code
    assert is_lock_busy(error) is expected


def test_error_text_is_not_a_retry_signal():
    assert not is_lock_busy(sqlite3.OperationalError('database is locked'))
    assert not is_lock_busy(RuntimeError('database is locked'))
