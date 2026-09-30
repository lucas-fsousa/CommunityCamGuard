"""Bounded codec metadata cache; never retain media bytes or cache probe failures."""

import threading
import time
from collections import OrderedDict
from collections.abc import Callable
from pathlib import Path

from .playback_budget import PlaybackBusy

_LIMIT = 256
_LOCK = threading.Lock()
_VALUES: OrderedDict[tuple[str, int, int, int, int, int], str] = OrderedDict()
# Fixed stripes avoid an unbounded per-file lock registry. Collisions only wait;
# they never share metadata. Limit subprocesses even for different recordings.
_STRIPES = tuple(threading.Lock() for _ in range(32))
_PROBES = threading.BoundedSemaphore(2)
ADMISSION_SECONDS = 1.0


def _identity(path: Path) -> tuple[str, int, int, int, int, int]:
    stat = path.stat()
    return (str(path), stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns)


def lookup(segment: Path, probe: Callable[[Path], str]) -> str:
    """Coalesce successful same-file probes; allow at most two probes at once.

    The supplied probe must be bounded (production ffprobe times out after 10s).
    No global metadata lock is held over subprocess I/O, and failures are not cached.
    """
    path = segment.resolve()
    deadline = time.monotonic() + ADMISSION_SECONDS
    stripe = _STRIPES[hash(str(path)) % len(_STRIPES)]
    if not stripe.acquire(timeout=ADMISSION_SECONDS):
        raise PlaybackBusy("codec inspection wait budget exhausted")
    try:
        return _lookup(path, probe, deadline)
    finally:
        stripe.release()


def _probe(path: Path, probe: Callable[[Path], str], deadline: float) -> str:
    if not _PROBES.acquire(timeout=max(0.0, deadline - time.monotonic())):
        raise PlaybackBusy("codec inspection wait budget exhausted")
    try:
        return probe(path)
    finally:
        _PROBES.release()


def _lookup(path: Path, probe: Callable[[Path], str], deadline: float) -> str:
    try:
        key = _identity(path)
    except OSError:
        return _probe(path, probe, deadline)
    with _LOCK:
        if key in _VALUES:
            _VALUES.move_to_end(key)
            return _VALUES[key]
    value = _probe(path, probe, deadline)
    if not value:
        return value
    try:
        if _identity(path) != key:
            return value  # A growing/replaced file must not publish stale metadata.
    except OSError:
        return value
    with _LOCK:
        _VALUES[key] = value
        _VALUES.move_to_end(key)
        while len(_VALUES) > _LIMIT:
            _VALUES.popitem(last=False)
    return value
