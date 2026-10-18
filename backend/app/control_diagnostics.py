"""Bounded persistent control diagnostics, never payloads or exception text."""

from __future__ import annotations

import json
import logging
import re
from datetime import UTC, datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path

_log = logging.getLogger("ccg.control_diagnostics")
_STAGES = {"session", "preflight", "write", "readback", "complete", "api"}
_OUTCOMES = {"started", "failed", "confirmed", "unchanged", "rejected"}


class _SafeHandler(RotatingFileHandler):
    def handleError(self, record: logging.LogRecord) -> None:
        # A full/unwritable disk must not abort a camera operation or dump context.
        pass


def configure(directory: Path) -> None:
    path = (directory / "control-events.jsonl").resolve()
    if any(isinstance(h, _SafeHandler) and h.baseFilename == str(path) for h in _log.handlers):
        return
    try:
        directory.mkdir(parents=True, exist_ok=True)
        handler = _SafeHandler(path, maxBytes=1024 * 1024, backupCount=3, encoding="utf-8")
    except OSError:
        logging.getLogger(__name__).warning("persistent control diagnostics unavailable")
        return
    for old in list(_log.handlers):
        _log.removeHandler(old)
        old.close()
    handler.setFormatter(logging.Formatter("%(message)s"))
    _log.addHandler(handler)
    _log.setLevel(logging.INFO)
    _log.propagate = False


def record(
    camera_id: str | None, *, stage: str, outcome: str,
    operation: str = "", error: int | None = None,
) -> None:
    """Orientation-only instrumentation; free-form data cannot reach the sink."""
    event = {
        "at": datetime.now(UTC).isoformat(timespec="milliseconds"),
        "camera_id": camera_id if isinstance(camera_id, str) and re.fullmatch(r"cam_[0-9a-f]{24}", camera_id) else None,
        "control": "orientation",
        "stage": stage if stage in _STAGES else "unknown",
        "outcome": outcome if outcome in _OUTCOMES else "unknown",
        "operation": operation if re.fullmatch(r"[0-9a-f]{32}", operation) else None,
        "error_code": error if type(error) is int and -(2**31) <= error < 2**31 else None,
    }
    _log.info(json.dumps(event, separators=(",", ":")))
