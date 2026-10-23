"""Owned runtime bindings without a source scheduler or real camera traffic."""

import json
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import Mock

import pytest
from _rtsp_fixture import CameraFixture, describe

from backend.app.db import registry, source_policy
from backend.app.media import go2rtc
from backend.app.media.browser_ingress import admit_hub
from backend.app.media.go2rtc import Go2rtc
from backend.app.media.owned_bindings import OwnedIngressBindings
from backend.app.media.rtsp_ingress import OwnedRtspIngress
from backend.app.media.source_registry import SourceCleanupUnconfirmed, SourceUnavailable
from backend.app.recording.recorder import Recorder

BINARY = Path(__file__).parents[1] / "bin/go2rtc"


@pytest.fixture
def cameras():
    registry.init_db()
    return [registry.upsert_camera(f"aa:bb:cc:dd:ee:0{i}", last_ip=f"192.0.2.{i}",
                                    stream_path="/synthetic") for i in (3, 4)]


def select_owned(camera):
    pending = source_policy.begin_change(camera.camera_id, "owned_rtsp", expected_revision=0)
    return source_policy.activate(camera.camera_id, expected_revision=pending.revision)


def ready_worker(camera, *, generation="a"):
    worker = OwnedRtspIngress(camera.camera_id, camera.rtsp_url, binary=BINARY)
    worker._process = Mock(pid=12345)
    worker._url = "rtsp://127.0.0.1:12345/ingress_" + generation * 32
    return worker


def test_renderer_recorder_and_viewer_share_binding_and_withdraw_without_fallback(cameras, tmp_path, monkeypatch):
    camera, other = cameras
    select_owned(camera)
    worker = ready_worker(camera)
    bindings = OwnedIngressBindings()
    binding = bindings.attach(worker)
    engine = Go2rtc(manage=False, config_path=tmp_path / "config.json", owned_ingress=bindings)
    engine.write_config(cameras)
    streams = json.loads(engine.config_path.read_text())["streams"]
    assert streams[camera.camera_id] == worker.restream_url
    assert streams[other.camera_id] == other.rtsp_url
    assert camera.rtsp_url not in engine.config_path.read_text()
    assert camera.camera_id + "_hd" in streams and camera.camera_id + "_web" in streams
    # Losing runtime bindings after restart never restores the direct camera URL.
    fresh = Go2rtc(manage=False, config_path=tmp_path / "fresh.json")
    fresh.write_config(cameras)
    assert camera.camera_id not in json.loads(fresh.config_path.read_text())["streams"]
    viewer = admit_hub(camera.camera_id, bindings)
    assert viewer is not None and viewer.valid()
    recorder = Recorder(owned_ingress=bindings)
    spawn = Mock(return_value=Mock())
    monkeypatch.setattr(recorder, "_spawn", spawn)
    with recorder._lock:
        recorder._spawn_locked(camera.camera_id)
    assert spawn.call_count == 1
    recorder.ingress.suspend(camera.camera_id)
    assert bindings.withdraw(camera.camera_id)
    assert binding.url is None and not viewer.valid() and admit_hub(camera.camera_id, bindings) is None
    engine.write_config(cameras)
    assert camera.camera_id not in json.loads(engine.config_path.read_text())["streams"]
    recorder.ingress.resume(camera.camera_id)
    with recorder._lock:
        recorder._spawn_locked(camera.camera_id)
    assert spawn.call_count == 1


def test_uncertain_child_retains_capacity_and_prevents_replacement(cameras):
    camera, other = cameras
    select_owned(camera)
    select_owned(other)
    bindings = OwnedIngressBindings()
    old = ready_worker(camera)
    bindings.attach(old)
    bindings.withdraw(camera.camera_id)
    with pytest.raises(SourceCleanupUnconfirmed):
        bindings.discard(camera.camera_id)
    with pytest.raises(SourceUnavailable):
        bindings.attach(ready_worker(other))
    with pytest.raises(SourceUnavailable):
        bindings.attach(ready_worker(camera, generation="b"))
    old._process = None  # mocked confirmed reap, not merely a stop signal
    bindings.discard(camera.camera_id)
    assert bindings.attach(ready_worker(other)).url is not None


def test_policy_revision_change_and_worker_exit_hide_binding(cameras):
    camera = cameras[0]
    selection = select_owned(camera)
    bindings = OwnedIngressBindings()
    worker = ready_worker(camera)
    bindings.attach(worker)
    assert bindings.binding(camera.camera_id, selection) is not None
    pending = source_policy.begin_change(camera.camera_id, "owned_rtsp", expected_revision=selection.revision)
    active = source_policy.activate(camera.camera_id, expected_revision=pending.revision)
    assert bindings.binding(camera.camera_id, active) is None
    worker._process = None
    assert bindings.binding(camera.camera_id, selection) is None


def test_only_ready_workers_for_active_owned_selection_can_attach(cameras):
    camera = cameras[0]
    bindings = OwnedIngressBindings()
    with pytest.raises(SourceUnavailable):
        bindings.attach(ready_worker(camera))
    select_owned(camera)
    worker = ready_worker(camera)
    worker._url = None
    with pytest.raises(SourceUnavailable):
        bindings.attach(worker)
    registry.delete_camera_by_id(camera.camera_id)
    with pytest.raises(SourceUnavailable):
        bindings.attach(ready_worker(camera))


@pytest.mark.parametrize("operation", ["config", "recorder"])
def test_withdraw_waits_for_prior_config_write_or_consumer_birth(cameras, tmp_path, monkeypatch, operation):
    camera = cameras[0]
    select_owned(camera)
    bindings = OwnedIngressBindings()
    bindings.attach(ready_worker(camera))
    entered, release, withdrawing = threading.Event(), threading.Event(), threading.Event()

    def delayed(result):
        entered.set()
        assert release.wait(2)
        return result()

    if operation == "config":
        engine = Go2rtc(manage=False, config_path=tmp_path / "config.json", owned_ingress=bindings)
        original = go2rtc.write_private_config
        monkeypatch.setattr(go2rtc, "write_private_config",
                            lambda *args, **kwargs: delayed(lambda: original(*args, **kwargs)))
        def apply():
            return engine.write_config(cameras)
    else:
        recorder = Recorder(owned_ingress=bindings)
        monkeypatch.setattr(recorder, "_spawn", lambda _: delayed(Mock))
        def apply():
            with recorder._lock:
                recorder._spawn_locked(camera.camera_id)

    def withdraw():
        withdrawing.set()
        return bindings.withdraw(camera.camera_id)

    with ThreadPoolExecutor(max_workers=2) as pool:
        applying = pool.submit(apply)
        try:
            assert entered.wait(2)
            retiring = pool.submit(withdraw)
            assert withdrawing.wait(1)
            with pytest.raises(TimeoutError):
                retiring.result(timeout=0.1)
        finally:
            release.set()
        applying.result(timeout=3)
        assert retiring.result(timeout=3)


@pytest.mark.parametrize("capacity", [0, 33, True, -1])
def test_binding_capacity_is_bounded(capacity):
    with pytest.raises(ValueError):
        OwnedIngressBindings(capacity=capacity)


@pytest.mark.skipif(sys.platform != "linux" or not BINARY.is_file(), reason="pinned Linux go2rtc fixture required")
def test_real_ready_worker_can_bind_render_serve_metadata_then_retire(cameras, tmp_path):
    camera = cameras[0]
    select_owned(camera)
    bindings = OwnedIngressBindings()
    with CameraFixture() as fixture, ThreadPoolExecutor(max_workers=1) as pool:
        worker = OwnedRtspIngress(camera.camera_id, fixture.url, binary=BINARY)
        future = pool.submit(worker.run, cancelled=lambda: False)
        try:
            deadline = time.monotonic() + 6
            while worker.restream_url is None and not future.done() and time.monotonic() < deadline:
                time.sleep(0.01)
            if future.done():
                future.result()
            binding = bindings.attach(worker)
            engine = Go2rtc(manage=False, config_path=tmp_path / "hub.json", owned_ingress=bindings)
            engine.write_config(cameras)
            url = json.loads(engine.config_path.read_text())["streams"][camera.camera_id]
            assert url == binding.url and fixture.accepted == 0
            with describe(url), describe(url):
                assert fixture.accepted == fixture.peak == 1
            bindings.withdraw(camera.camera_id)
            with pytest.raises(SourceCleanupUnconfirmed):
                bindings.discard(camera.camera_id)
        finally:
            worker.stop()
            assert future.result(timeout=6) == "stopped"
        bindings.discard(camera.camera_id)
        assert fixture.closed.wait(2)
        engine.write_config(cameras)
        assert camera.camera_id not in json.loads(engine.config_path.read_text())["streams"]
