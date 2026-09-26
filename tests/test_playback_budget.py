"""Archive work admission/serialization, using fake encoders only."""

import threading
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from backend.app.api import recordings
from backend.app.recording import playback, playback_budget


def test_thread_limits_apply_to_decoder_and_encoder():
    cmd = playback._ffmpeg_cmd(Path("in.mp4"), Path("out.mp4"))
    source = cmd.index("-i")
    assert cmd[:source].count("-threads") == cmd[source:].count("-threads") == 1
    for index, value in enumerate(cmd):
        if value in ("-threads", "-filter_threads", "-filter_complex_threads"):
            assert cmd[index + 1] == "1"
    assert "-nostdin" in cmd


def test_different_files_are_serialized_and_admission_is_bounded(monkeypatch, tmp_path):
    monkeypatch.setattr(playback, "needs_transcode", lambda _: True)
    entered, release = threading.Event(), threading.Event()
    lock = threading.Lock()
    active = peak = 0
    calls = []
    def encode(cmd, **kwargs):
        nonlocal active, peak
        with lock:
            active += 1
            peak = max(peak, active)
            calls.append(cmd)
        entered.set()
        assert release.wait(3)
        Path(cmd[-1]).write_bytes(b"derived")
        with lock:
            active -= 1
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(playback.subprocess, "run", encode)
    files = [tmp_path / f"{i}.mp4" for i in range(5)]
    jobs = []
    try:
        jobs.append(playback._prepare_job(files[0]))
        assert entered.wait(2)
        # The synchronous warmer shares an existing job; it cannot enqueue new work
        # ahead of pending foreground conversions.
        assert playback._prepare_job(files[0], background=True) is jobs[0]
        with pytest.raises(playback_budget.PlaybackBusy):
            playback._prepare_job(files[1], background=True)
        for file in files[1:4]:
            jobs.append(playback._prepare_job(file))
        assert playback._prepare_job(files[0]) is jobs[0]
        with pytest.raises(playback_budget.PlaybackBusy):
            playback.prepare_transcode(files[4])
        assert len(calls) == 1 and peak == 1
    finally:
        release.set()
        for job in jobs:
            assert job.done.wait(3)
    assert len(calls) == 4 and peak == 1
    assert not playback._JOBS


def test_queue_timeout_releases_job_without_starting_encoder(monkeypatch, tmp_path):
    monkeypatch.setattr(playback_budget, "QUEUE_WAIT_SECONDS", 0)
    monkeypatch.setattr(playback, "needs_transcode", lambda _: True)
    monkeypatch.setattr(playback.subprocess, "run", lambda *a, **k: pytest.fail("encoder started"))
    assert playback_budget._ENCODER.acquire(blocking=False)
    try:
        job = playback._prepare_job(tmp_path / "queued.mp4")
        assert job.done.wait(2)
        assert job.failed and not playback._JOBS
    finally:
        playback_budget._ENCODER.release()


def test_thread_start_failure_rolls_back_admission(monkeypatch, tmp_path):
    monkeypatch.setattr(playback, "needs_transcode", lambda _: True)
    def fail(_self):
        raise RuntimeError("cannot start")
    monkeypatch.setattr(playback._TranscodeJob, "start", fail)
    with pytest.raises(playback_budget.PlaybackBusy):
        playback.prepare_transcode(tmp_path / "x.mp4")
    assert not playback._JOBS


@pytest.mark.parametrize("endpoint", [recordings.recording_file, recordings.prepare_recording_playback])
def test_saturation_returns_retryable_http_status(monkeypatch, tmp_path, endpoint):
    target = tmp_path / "x.mp4"
    monkeypatch.setattr(recordings, "_recording_target", lambda _: (tmp_path, target))
    monkeypatch.setattr(playback, "cached_path", lambda _: None)
    monkeypatch.setattr(playback, "transcode_in_progress", lambda _: False)
    monkeypatch.setattr(playback, "needs_transcode", lambda _: True)
    attempts = []
    def busy(target):
        attempts.append(target)
        raise playback_budget.PlaybackBusy()
    monkeypatch.setattr(playback, "prepare_transcode", busy)
    with pytest.raises(HTTPException) as caught:
        endpoint("x")
    if endpoint is recordings.recording_file:
        assert caught.value.status_code == 409
        assert caught.value.headers == {"Cache-Control": "no-store"}
        assert not attempts
    else:
        assert caught.value.status_code == 429 and caught.value.headers == {"Retry-After": "5"}
        assert attempts == [target]
