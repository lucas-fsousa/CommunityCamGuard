"""Process-local authentication generation; no credentials or network operations.

Broker ownership is conservatively driver-wide, so its generation is too. A fresh
authentication attempt may supersede previously cached stopped routes even when
initialization later fails. External processes are not observable here.
"""
import threading

_lock = threading.Lock()
_epoch = 0


def current() -> int:
    with _lock:
        return _epoch


def authentication_started() -> None:
    global _epoch
    with _lock:
        _epoch += 1
