"""Read-only evidence classification shared by source and outer owner guards."""

import sqlite3
from collections.abc import Callable

from ...db.errors import is_lock_busy
from ...media.source_registry import SourceValidationBusy


def checked(read: Callable[[], bool]) -> bool:
    try:
        return read()
    except sqlite3.OperationalError as error:
        if not is_lock_busy(error):
            raise
        raise SourceValidationBusy('native registry read temporarily unavailable') from None
