"""Recording retention — a sporadic job that deletes footage past its retention window.

This is the deliberate counterpart to the storage monitor. The storage monitor (``storage.py``)
**never deletes** — it only pauses recording when the disk is nearly full. Retention is the
*time-based*, user-configured cleanup: ``RECORDING_RETENTION_DAYS`` says how many days of footage
to keep. ``0`` (the floor) means **keep forever** — the job is a no-op and footage only grows until
the storage monitor steps in. There is no upper bound; you keep as much as the disk holds.

Each pass removes every indexed segment whose start time is older than the cutoff — its file, its
derived playback-cache transcode, and its index row — then prunes the empty day/hour directories
left behind. It is **DB-driven**: the recordings index is authoritative, so we delete exactly what
the recordings page can list. A freshly written, not-yet-indexed segment is by definition recent,
so it is never in scope. A file that can't be removed keeps its row, so the next pass retries it.
"""
from __future__ import annotations

import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path

from ..config import get_settings
from ..db import connect
from . import playback

# Retention is day-granular, so a frequent sweep is pointless; run it sporadically.
DEFAULT_INTERVAL = 3600.0


def _rmdir_if_empty(path: Path) -> None:
    try:
        path.rmdir()            # only succeeds when the directory is empty
    except OSError:
        pass                    # non-empty (still in use) or already gone — leave it


class RetentionCleaner:
    """Periodically purges recordings older than ``recording_retention_days``."""

    def __init__(self, *, retention_days: int | None = None,
                 interval: float = DEFAULT_INTERVAL) -> None:
        s = get_settings()
        self.retention_days = s.recording_retention_days if retention_days is None else retention_days
        self.root = Path(s.recordings_dir)
        self.interval = interval
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def _cutoff_iso(self) -> str:
        # New segment names and started_at values are UTC.  Use the same basis for retention so
        # host/camera timezone changes cannot make footage expire early or late.  Legacy naive
        # rows still compare in chronological YYYY-MM-DDTHH:MM:SS order; there is no safe way to
        # infer which historical timezone produced them.
        return (datetime.now(UTC) - timedelta(days=self.retention_days)).isoformat(
            timespec="seconds"
        )

    def purge(self) -> int:
        """Delete every segment older than the retention window. Returns how many were removed."""
        if self.retention_days <= 0:
            return 0                                        # 0 = keep forever
        cutoff = self._cutoff_iso()
        with connect() as conn:
            expired = conn.execute(
                "SELECT id, path FROM recordings WHERE started_at < ?", (cutoff,)
            ).fetchall()
        purged_ids: list[int] = []
        for row in expired:
            seg = Path(row["path"])
            cache = playback.cache_path(seg)                # resolve before we unlink the source
            try:
                seg.unlink(missing_ok=True)
            except OSError:
                continue                                    # keep the row → retry next pass
            cache.unlink(missing_ok=True)                   # drop the derived transcode too
            purged_ids.append(row["id"])
        if purged_ids:
            with connect() as conn:
                conn.executemany("DELETE FROM recordings WHERE id = ?",
                                 [(i,) for i in purged_ids])
        self._prune_empty_dirs()
        return len(purged_ids)

    def _prune_empty_dirs(self) -> None:
        """Remove empty ``<mac>/<day>/<hour>`` and ``<mac>/<day>`` dirs left after deletions."""
        if not self.root.exists():
            return
        for cam in self.root.iterdir():
            if not cam.is_dir():
                continue
            for day in list(cam.iterdir()):
                if not day.is_dir():
                    continue
                for hour in list(day.iterdir()):
                    if hour.is_dir():
                        _rmdir_if_empty(hour)
                _rmdir_if_empty(day)                        # recorder recreates current dirs as needed

    # --- background loop ----------------------------------------------------------
    def start(self) -> None:
        if self.retention_days <= 0:
            return                                          # disabled → don't spin a thread
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5)

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self.purge()
            except Exception:               # never let a purge error kill the loop
                pass
            self._stop.wait(self.interval)
