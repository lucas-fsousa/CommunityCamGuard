"""Recording consumer gate with isolated registry and fake child processes."""

import subprocess
import threading
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from backend.app.db.registry import Camera
from backend.app.recording.ingress_gate import MAX_HOLDS
from backend.app.recording.process_lifecycle import RecorderCleanupUnconfirmed
from backend.app.recording.recorder import Recorder

CAMERA = "cam_" + "a" * 24
OTHER = "cam_" + "b" * 24


def camera(camera_id=CAMERA):
    return Camera(mac="aa:bb:cc:dd:ee:03" if camera_id == CAMERA else "aa:bb:cc:dd:ee:04",
                  camera_id=camera_id,
                  last_ip="10.0.0.5", stream_path="/synthetic")


def child():
    process = Mock()
    process.poll.return_value = None
    process.wait.return_value = 0
    return process


@pytest.fixture
def recorder(monkeypatch):
    rec = Recorder()
    rec._maint = SimpleNamespace(is_alive=lambda: True)
    monkeypatch.setattr(rec, "_spawn", Mock(side_effect=lambda _id: child()))
    return rec


def test_suspend_only_selected_camera_and_survives_registry_resync(recorder):
    first, other = child(), child()
    recorder._procs = {CAMERA: first, OTHER: other}
    recorder.ingress.suspend(CAMERA)
    assert recorder.ingress.is_suspended(CAMERA)
    first.terminate.assert_called_once()
    first.wait.assert_called_once()
    recorder.start([camera(), camera(OTHER)])
    assert recorder._procs == {OTHER: other}
    other.terminate.assert_not_called()
    recorder._spawn.assert_not_called()
    recorder.ingress.resume(CAMERA)
    recorder._spawn_locked(CAMERA)
    recorder._spawn.assert_called_once_with(CAMERA)
    assert recorder._procs[OTHER] is other


def test_failed_suspend_retains_hold_and_handle_until_explicit_confirmed_retry(recorder):
    process = child()
    process.wait.side_effect = subprocess.TimeoutExpired("synthetic", 5)
    recorder._procs[CAMERA] = process
    with pytest.raises(subprocess.TimeoutExpired):
        recorder.ingress.suspend(CAMERA)
    recorder.start([camera()])
    recorder._spawn.assert_not_called()
    assert recorder._procs[CAMERA] is process and recorder.ingress.is_suspended(CAMERA)
    # Even a later poll result cannot silently release a failed suspension.
    process.poll.return_value = 0
    with pytest.raises(RecorderCleanupUnconfirmed):
        recorder.ingress.resume(CAMERA)
    process.wait.side_effect = None
    recorder.ingress.suspend(CAMERA)
    recorder.ingress.resume(CAMERA)
    assert not recorder.ingress.is_suspended(CAMERA) and not recorder._procs


def test_global_storage_resume_does_not_release_per_camera_source_hold(recorder):
    recorder.ingress.suspend(CAMERA)
    recorder.pause()
    recorder.start([camera()])
    recorder.resume()
    recorder._spawn_locked(CAMERA)
    recorder._spawn.assert_not_called()
    assert recorder.ingress.is_suspended(CAMERA)


def test_hold_capacity_and_invalid_ids_never_allocate_unbounded_history(recorder):
    for index in range(MAX_HOLDS):
        recorder.ingress.suspend(f"cam_{index:024x}")
    with pytest.raises(RuntimeError, match="capacity"):
        recorder.ingress.suspend(CAMERA)
    assert not recorder.ingress.is_suspended(CAMERA)
    for invalid in ("native-123", "", "../camera"):
        with pytest.raises(ValueError):
            recorder.ingress.suspend(invalid)
    recorder.ingress.suspend("cam_" + "0" * 24)  # existing hold can retry at capacity
    recorder.ingress.resume("cam_" + "0" * 24)
    recorder.ingress.suspend(CAMERA)
    assert recorder.ingress.is_suspended(CAMERA)


def test_suspend_serializes_against_an_already_starting_consumer(recorder, monkeypatch):
    entered, release = threading.Event(), threading.Event()
    process = child()

    def spawn(_camera):
        entered.set()
        assert release.wait(2)
        return process

    monkeypatch.setattr(recorder, "_spawn", spawn)
    with ThreadPoolExecutor(max_workers=2) as pool:
        starting = pool.submit(recorder.start, [camera()])
        assert entered.wait(2)
        suspension = pool.submit(recorder.ingress.suspend, CAMERA)
        release.set()
        starting.result(timeout=3)
        suspension.result(timeout=3)
    assert recorder.ingress.is_suspended(CAMERA) and CAMERA not in recorder._procs
    process.terminate.assert_called_once()
    process.wait.assert_called_once()
