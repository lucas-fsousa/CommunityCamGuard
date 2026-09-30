"""Bounded codec metadata cache; never retain media bytes or cache probe failures."""

import threading
from collections import OrderedDict
from collections.abc import Callable
from pathlib import Path

_LIMIT = 256
_LOCK = threading.Lock()
_VALUES: OrderedDict[tuple[str, int, int, int, int, int], str] = OrderedDict()
# Fixed stripes avoid an unbounded per-file lock registry. Collisions only wait;
# they never share metadata. Limit subprocesses even for different recordings.
_STRIPES = tuple(threading.Lock() for _ in range(32))
_PROBES = threading.BoundedSemaphore(2)


def _identity(path: Path) -> tuple[str, int, int, int, int, int]:
    stat = path.stat()
    return (str(path), stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns)


def lookup(segment: Path, probe: Callable[[Path], str]) -> str:
    """Coalesce successful same-file probes; allow at most two probes at once.

    The supplied probe must be bounded (production ffprobe times out after 10s).
    No global metadata lock is held over subprocess I/O, and failures are not cached.
    """
    path = segment.resolve()
    with _STRIPES[hash(str(path)) % len(_STRIPES)]:
        return _lookup(path, probe)


def _lookup(path: Path, probe: Callable[[Path], str]) -> str:
    try:
        key = _identity(path)
    except OSError:
        with _PROBES:
            return probe(path)
    with _LOCK:
        if key in _VALUES:
            _VALUES.move_to_end(key)
            return _VALUES[key]
    with _PROBES:
        value = probe(path)
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
