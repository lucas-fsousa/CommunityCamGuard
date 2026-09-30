"""Version derived media by source identity without reading media into memory."""

import hashlib
from pathlib import Path


def signature(path: Path) -> tuple[int, int, int, int, int] | None:
    try:
        stat = path.stat()
    except OSError:
        return None
    return (stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns)


def cache_key(path: Path) -> str:
    source = path.resolve()
    # Versioned namespace: old path-only entries cannot prove source freshness.
    value = repr(("playback-v2", str(source), signature(source)))
    return hashlib.sha256(value.encode()).hexdigest()[:24]
