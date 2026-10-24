"""Deadline-limited synchronous waits shared by prepared source owners."""

import time
from _thread import LockType
from collections.abc import Iterator
from contextlib import contextmanager

from .source_registry import SourceCleanupUnconfirmed


def remaining(deadline: float) -> float:
    budget = deadline - time.monotonic()
    if budget <= 0:
        raise SourceCleanupUnconfirmed("source cleanup deadline exceeded")
    return budget


@contextmanager
def locked_until(lock: LockType, deadline: float) -> Iterator[None]:
    if not lock.acquire(timeout=remaining(deadline)):
        raise SourceCleanupUnconfirmed("source cleanup lock unavailable")
    try:
        yield
    finally:
        lock.release()
