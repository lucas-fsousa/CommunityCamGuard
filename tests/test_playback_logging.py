"""Scoped archive metrics: visible with server defaults, bounded and content-free."""

import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from backend.app.recording import playback, playback_budget


def test_server_default_logging_exposes_only_scoped_metrics_once():
    # Isolate global logging configuration from pytest and other ASGI tests.
    script = '''
import logging
import logging.config
from uvicorn.config import LOGGING_CONFIG
from backend.app.recording.playback_logging import configure, LOGGER_NAME
logging.config.dictConfig(LOGGING_CONFIG)
root_level = logging.getLogger().level
configure()
configure()
logging.getLogger(LOGGER_NAME).info("playback_prepare outcome=ready")
logging.getLogger("backend.app.drivers").info("sdk_private_sentinel")
assert logging.getLogger().level == root_level
'''
    result = subprocess.run([sys.executable, "-c", script], capture_output=True,
                            text=True, timeout=10)
    assert result.returncode == 0, result.stderr
    assert result.stderr.count("playback_prepare outcome=ready") == 1
    assert "sdk_private_sentinel" not in result.stdout + result.stderr


@pytest.mark.parametrize("case,reason", [
    ("ready", "none"),
    ("cached", "cache_hit"),
    ("queue", "queue_timeout"),
    ("timeout", "encode_timeout"),
    ("failed", "encoder_failed"),
    ("missing", "output_missing"),
    ("io", "io_or_process_error"),
    ("cleanup", "cleanup_failed"),
])
def test_job_reports_safe_reason_and_always_releases(monkeypatch, tmp_path, case, reason):
    messages = []
    monkeypatch.setattr(playback.log, "info", lambda fmt, *args: messages.append(fmt % args))
    job = playback._TranscodeJob(tmp_path / "private-camera-secret.mp4")
    if case == "cached":
        job.cache.write_bytes(b"cached")

    def encode(cmd, **kwargs):
        if case == "timeout":
            raise subprocess.TimeoutExpired("private-command-secret", 600)
        if case == "io":
            raise OSError("private-error-secret")
        if case not in ("failed", "missing"):
            Path(cmd[-1]).write_bytes(b"derived")
        return SimpleNamespace(returncode=1 if case == "failed" else 0)

    monkeypatch.setattr(playback.subprocess, "run", encode)
    if case == "queue":
        def busy():
            raise playback_budget.PlaybackBusy("private-queue-secret")
        monkeypatch.setattr(playback, "encoder_slot", busy)
    if case == "cleanup":
        original_unlink = Path.unlink
        def unlink(path, *args, **kwargs):
            if path == job.part:
                raise OSError("private-cleanup-secret")
            return original_unlink(path, *args, **kwargs)
        monkeypatch.setattr(Path, "unlink", unlink)
    playback._JOBS[job.segment] = job
    job._run()
    assert job.done.is_set() and job.segment not in playback._JOBS
    assert job.failed == (case not in ("ready", "cached"))
    assert len(messages) == 1
    message = messages[0]
    assert f"reason={reason} " in message
    assert "outcome=" + ("failed" if job.failed else "ready") in message
    assert "secret" not in message and str(tmp_path) not in message
    assert "queue_ms=" in message and "encode_ms=" in message
