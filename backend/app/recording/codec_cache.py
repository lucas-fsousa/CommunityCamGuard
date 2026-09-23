"""Bounded codec metadata cache; never retain media bytes or cache probe failures."""

import threading
from collections import OrderedDict
from collections.abc import Callable
from pathlib import Path

_LIMIT = 256
_LOCK = threading.Lock()
_VALUES: OrderedDict[tuple[str, int, int, int, int, int], str] = OrderedDict()


def _identity(path: Path) -> tuple[str, int, int, int, int, int]:
    stat = path.stat()
    return (str(path), stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns)


def lookup(segment: Path, probe: Callable[[Path], str]) -> str:
    """Concurrent misses may probe independently; no lock is held over subprocess I/O."""
    try:
        path = segment.resolve()
        key = _identity(path)
    except OSError:
        return probe(segment)
    with _LOCK:
        if key in _VALUES:
            _VALUES.move_to_end(key)
            return _VALUES[key]
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
