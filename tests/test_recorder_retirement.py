"""Retain recorder ownership until exit is confirmed; no real FFmpeg/cameras."""

import subprocess
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from backend.app.db.registry import Camera
from backend.app.recording import recorder as module
from backend.app.recording.process_lifecycle import RecorderCleanupUnconfirmed, retire_process

CAMERA = "cam_" + "a" * 24
OTHER = "cam_" + "b" * 24


def child(*, uncertain=False):
    process = Mock()
    process.pid = 999999
    process.poll.return_value = None
    if uncertain:
        process.wait.side_effect = subprocess.TimeoutExpired("synthetic", 5)
    else:
        process.wait.return_value = 0
    return process


@pytest.fixture
def recorder(monkeypatch):
    value = module.Recorder()
    # Reconciliation tests must not start a maintenance thread.
    value._maint = SimpleNamespace(is_alive=lambda: True)
    monkeypatch.setattr(value, "_spawn", Mock(side_effect=AssertionError("unexpected replacement")))
    return value


def test_force_kill_requires_second_wait():
    process = child()
    process.wait.side_effect = [subprocess.TimeoutExpired("synthetic", 5), -9]
    retire_process(process)
    process.terminate.assert_called_once()
    process.kill.assert_called_once()
    assert process.wait.call_count == 2


def test_failed_watchdog_wait_keeps_process_and_does_not_spawn_duplicate(recorder, monkeypatch):
    process = child(uncertain=True)
    recorder._procs[CAMERA] = process
    recorder._spawned_at[CAMERA] = 10
    monkeypatch.setattr(module, "_rss_bytes", lambda _pid: module._RSS_LIMIT_BYTES + 1)
    recorder._watchdog_locked()
    recorder._spawn_locked(CAMERA)
    assert recorder._procs[CAMERA] is process
    assert CAMERA not in recorder._fails
    process.kill.assert_called_once()
    recorder._spawn.assert_not_called()


def test_batch_pause_retains_uncertain_child_but_finishes_other_cleanup(recorder):
    failed, healthy = child(uncertain=True), child()
    recorder._procs = {CAMERA: failed, OTHER: healthy}
    with pytest.raises(RecorderCleanupUnconfirmed):
        recorder.pause()
    assert recorder.paused and recorder._procs == {CAMERA: failed}
    assert failed.wait.call_count == 2
    healthy.terminate.assert_called_once()
    healthy.wait.assert_called_once()
    with pytest.raises(RecorderCleanupUnconfirmed):
        recorder.resume()
    assert recorder.paused and recorder._procs[CAMERA] is failed
    # Explicit resume first finishes the prior pause, then admits future work.
    failed.wait.side_effect = None
    failed.wait.return_value = -9
    recorder.resume()
    assert not recorder.paused and recorder._procs == {}
    recorder._spawn.assert_not_called()


def test_repeat_pause_retries_failed_cleanup(recorder):
    process = child(uncertain=True)
    recorder._procs[CAMERA] = process
    with pytest.raises(RecorderCleanupUnconfirmed):
        recorder.pause()
    process.wait.side_effect = None
    process.wait.return_value = 0
    recorder.pause()
    assert recorder.paused and not recorder._procs
    assert process.terminate.call_count == 2


def test_camera_removal_does_not_forget_uncertain_child(recorder):
    process = child(uncertain=True)
    recorder._procs[CAMERA] = process
    with pytest.raises(subprocess.TimeoutExpired):
        recorder.start([])
    assert recorder._procs[CAMERA] is process
    process.wait.side_effect = None
    process.wait.return_value = -9
    recorder.start([])
    assert not recorder._procs


def test_registry_resync_preserves_storage_pause(recorder):
    recorder.pause()
    camera = Camera(mac="aa:bb:cc:dd:ee:03", camera_id=CAMERA,
                    last_ip="10.0.0.5", stream_path="/fake")
    assert recorder.start([camera]) == [CAMERA]
    assert recorder.paused and not recorder._procs
    recorder._spawn.assert_not_called()
