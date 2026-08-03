"""Disk-usage guard for the recordings volume — it never deletes anything.

Policy (see docs/DECISIONS.md), two thresholds on the recordings filesystem:

- **alert** (default 80%): raise a visible warning but keep recording.
- **full** (default 98%): stop *saving* — pause the recorder — while go2rtc keeps
  streaming; auto-resume once usage drops back below the **resume** mark. The gap between
  full and resume is hysteresis, so we don't flap the recorder on/off at the boundary.

Deleting old footage is always the user's call, never the app's.
"""
from __future__ import annotations

import shutil
import threading
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from ..config import get_settings
from .recorder import Recorder

OK = "ok"
ALERT = "alert"
FULL = "full"


@dataclass
class StorageState:
    total_bytes: int
    used_bytes: int
    free_bytes: int
    used_percent: float
    status: str            # OK | ALERT | FULL
    saving_paused: bool
    checked_at: str


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


class StorageMonitor:
    """Watches the recordings filesystem and pauses/resumes the recorder by policy."""

    def __init__(self, recorder: Recorder | None = None, *, path: Path | None = None,
                 alert_percent: int | None = None, full_percent: int | None = None,
                 resume_percent: int | None = None, interval: float | None = None) -> None:
        s = get_settings()
        self.recorder = recorder
        self.path = Path(path or s.recordings_dir)
        self.alert_percent = s.storage_alert_percent if alert_percent is None else alert_percent
        self.full_percent = s.storage_full_percent if full_percent is None else full_percent
        self.resume_percent = s.storage_resume_percent if resume_percent is None else resume_percent
        self.interval = s.storage_check_seconds if interval is None else interval
        self._paused_by_full = False
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def _used_percent(self) -> tuple[int, int, int, float]:
        self.path.mkdir(parents=True, exist_ok=True)
        du = shutil.disk_usage(self.path)
        pct = (du.used / du.total * 100) if du.total else 0.0
        return du.total, du.used, du.free, pct

    def state(self) -> StorageState:
        """Current storage state (read-only; for the dashboard/API)."""
        total, used, free, pct = self._used_percent()
        if pct >= self.full_percent or self._paused_by_full:
            status = FULL
        elif pct >= self.alert_percent:
            status = ALERT
        else:
            status = OK
        paused = self.recorder.paused if self.recorder else self._paused_by_full
        return StorageState(total, used, free, round(pct, 1), status, paused, _now())

    def check(self) -> StorageState:
        """Evaluate once and apply the full/resume policy to the recorder."""
        _, _, _, pct = self._used_percent()
        if not self._paused_by_full and pct >= self.full_percent:
            self._paused_by_full = True
            if self.recorder is not None:
                self.recorder.pause()
        elif self._paused_by_full and pct <= self.resume_percent:
            self._paused_by_full = False
            if self.recorder is not None:
                self.recorder.resume()
        return self.state()

    # --- background loop ----------------------------------------------------------
    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=self.interval + 1)

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self.check()
            except OSError:
                pass
            self._stop.wait(self.interval)
