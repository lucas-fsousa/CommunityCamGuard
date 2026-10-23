"""Durable source intent and read/apply barriers; no real media/camera processes."""

import json
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from unittest.mock import Mock

import pytest

from backend.app.db import connect, registry, source_policy
from backend.app.media import go2rtc
from backend.app.recording.recorder import Recorder


@pytest.fixture
def cameras():
    registry.init_db()
    return [registry.upsert_camera(f"aa:bb:cc:dd:ee:0{number}", last_ip=f"10.0.0.{number}",
                                    stream_path="/synthetic") for number in (3, 4)]


def test_missing_policy_is_read_only_legacy_compatibility(cameras):
    camera_id = cameras[0].camera_id
    assert source_policy.read_for((camera_id,))[camera_id].permits_legacy_rtsp
    with closing(connect()) as conn:
        assert conn.execute("SELECT 1 FROM sqlite_master WHERE name='media_source_policy'").fetchone() is None


@pytest.mark.parametrize("mode", ["native", "owned_rtsp"])
def test_new_renderer_does_not_restore_direct_camera_after_transition_or_activation(cameras, tmp_path, mode):
    camera_id, other = (camera.camera_id for camera in cameras)
    path = tmp_path / "worker-config.json"
    engine = go2rtc.Go2rtc(manage=False, config_path=path)
    engine.write_config(cameras)
    assert camera_id in json.loads(path.read_text())["streams"]
    pending = source_policy.begin_change(camera_id, mode, expected_revision=0)
    assert pending.phase == "transition"
    # New instance models lost in-memory bindings after server restart.
    restarted = go2rtc.Go2rtc(manage=False, config_path=path)
    restarted.write_config(cameras)
    streams = json.loads(path.read_text())["streams"]
    assert camera_id not in streams and camera_id + "_hd" not in streams
    assert camera_id + "_web" not in streams and other in streams
    source_policy.activate(camera_id, expected_revision=pending.revision)
    restarted.write_config(cameras)
    assert camera_id not in json.loads(path.read_text())["streams"]


def test_cas_and_uncertain_cleanup_cannot_optimistically_reset_selection(cameras):
    camera_id = cameras[0].camera_id
    pending = source_policy.begin_change(camera_id, "native", expected_revision=0)
    with pytest.raises(source_policy.SourcePolicyConflict):
        source_policy.begin_change(camera_id, "owned_rtsp", expected_revision=0)
    blocked = source_policy.block(camera_id, expected_revision=pending.revision)
    with pytest.raises(source_policy.SourcePolicyConflict):
        source_policy.activate(camera_id, expected_revision=blocked.revision)
    with pytest.raises(source_policy.SourcePolicyConflict):
        source_policy.begin_change(camera_id, "legacy_rtsp", expected_revision=blocked.revision)
    assert not source_policy.read_for((camera_id,))[camera_id].permits_legacy_rtsp


def test_corrupt_selection_is_blocked_only_for_its_camera(cameras):
    camera_id, other = (camera.camera_id for camera in cameras)
    source_policy.begin_change(camera_id, "native", expected_revision=0)
    with closing(connect()) as conn, conn:
        conn.execute("UPDATE media_source_policy SET mode='unknown', phase='active' WHERE camera_id=?",
                     (camera_id,))
    selection = source_policy.read_for((camera_id,))[camera_id]
    assert selection.phase == "blocked" and selection.revision == -1
    streams = go2rtc.build_config(cameras)["streams"]
    assert camera_id not in streams and other in streams


def test_dhcp_registry_update_cannot_reset_source_intent(cameras):
    camera, other = cameras
    source_policy.begin_change(camera.camera_id, "native", expected_revision=0)
    registry.upsert_camera(camera.mac, last_ip="10.0.0.99")
    streams = go2rtc.build_config()["streams"]
    assert camera.camera_id not in streams and other.camera_id in streams


def test_fresh_recorder_obeys_policy_without_in_memory_hold(cameras, monkeypatch):
    camera_id = cameras[0].camera_id
    source_policy.begin_change(camera_id, "native", expected_revision=0)
    recorder = Recorder()
    spawn = Mock(side_effect=AssertionError("retired direct-camera recorder resurrected"))
    monkeypatch.setattr(recorder, "_spawn", spawn)
    assert not recorder.ingress.is_suspended(camera_id)
    with recorder._lock:
        recorder._spawn_locked(camera_id)
    spawn.assert_not_called()


def test_policy_commit_waits_until_prior_config_write_finishes(cameras, tmp_path, monkeypatch):
    camera_id = cameras[0].camera_id
    source_policy.init_db()
    entered, release, changing = threading.Event(), threading.Event(), threading.Event()
    original = go2rtc.write_private_config
    engine = go2rtc.Go2rtc(manage=False, config_path=tmp_path / "config.json")

    def delayed(path, rendered, **kwargs):
        entered.set()
        assert release.wait(2)
        return original(path, rendered, **kwargs)

    def change():
        changing.set()
        return source_policy.begin_change(camera_id, "native", expected_revision=0)

    monkeypatch.setattr(go2rtc, "write_private_config", delayed)
    with ThreadPoolExecutor(max_workers=2) as pool:
        writing = pool.submit(engine.write_config, cameras)
        assert entered.wait(2)
        transition = pool.submit(change)
        try:
            assert changing.wait(1)
            with pytest.raises(TimeoutError):
                transition.result(timeout=0.1)
        finally:
            release.set()
        writing.result(timeout=3)
        assert transition.result(timeout=3).phase == "transition"
    engine.write_config(cameras)
    assert camera_id not in json.loads(engine.config_path.read_text())["streams"]


def test_policy_commit_serializes_with_consumer_birth_then_blocks_respawn(cameras, monkeypatch):
    camera_id = cameras[0].camera_id
    source_policy.init_db()
    recorder = Recorder()
    entered, release, changing = threading.Event(), threading.Event(), threading.Event()
    process = Mock()
    process.poll.return_value = None
    process.wait.return_value = 0

    def spawn(_id):
        entered.set()
        assert release.wait(2)
        return process

    def admit():
        with recorder._lock:
            recorder._spawn_locked(camera_id)

    def change():
        changing.set()
        return source_policy.begin_change(camera_id, "native", expected_revision=0)

    mock = Mock(side_effect=spawn)
    monkeypatch.setattr(recorder, "_spawn", mock)
    with ThreadPoolExecutor(max_workers=2) as pool:
        admitting = pool.submit(admit)
        assert entered.wait(2)
        transition = pool.submit(change)
        try:
            assert changing.wait(1)
            with pytest.raises(TimeoutError):
                transition.result(timeout=0.1)
        finally:
            release.set()
        admitting.result(timeout=3)
        transition.result(timeout=3)
    assert recorder._procs[camera_id] is process
    recorder.ingress.suspend(camera_id)
    recorder.ingress.resume(camera_id)
    admit()  # policy survives release of the transient recorder gate
    assert mock.call_count == 1 and not recorder._procs


def test_storage_failure_never_falls_back_to_raw_camera_url(cameras, monkeypatch):
    def offline():
        raise sqlite3.OperationalError("synthetic storage failure")

    monkeypatch.setattr(source_policy, "connect", offline)
    with pytest.raises(sqlite3.Error):
        go2rtc.build_config(cameras)
