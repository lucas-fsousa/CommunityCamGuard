"""Bounded UTC directory preparation for one caller-owned archive writer."""

import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path

from .archive_names import ARCHIVE_TEMPLATE


class ArchiveDirectories:
    """No thread, capture or deletion; the writer's consumer owns refresh calls."""

    def __init__(self, template: Path, *, clock: Callable[[], float] = time.monotonic):
        if template.name != ARCHIVE_TEMPLATE:
            raise ValueError("timestamped UTC archive template required")
        self._parent = str(template.parent)
        self._clock = clock
        self._next_refresh = 0.0

    def prepare(self, *, force: bool = False) -> None:
        if not force and self._clock() < self._next_refresh:
            return
        now = datetime.now(UTC)
        # FFmpeg's segment muxer cannot mkdir. Match its UTC strftime expansion,
        # preparing a full day ahead without scanning existing footage.
        directories = {Path((now + timedelta(hours=offset)).strftime(self._parent))
                       for offset in range(25)}
        for directory in sorted(directories):
            directory.mkdir(parents=True, exist_ok=True)
        # Advance only after success: a failed filesystem operation is not ready.
        self._next_refresh = self._clock() + 30
