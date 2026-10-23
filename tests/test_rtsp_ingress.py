"""Owned ingress config/lifecycle; real binary tests only use loopback sockets."""

import json
import os
import socket
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import Mock

import pytest
from _rtsp_fixture import CameraFixture, describe

from backend.app.media.rtsp_ingress import OwnedRtspIngress
from backend.app.media.rtsp_ingress_config import sealed_config, worker_config
from backend.app.media.source_registry import SourceCleanupUnconfirmed, SourceUnavailable

CAMERA = "cam_" + "c" * 24
BINARY = Path(__file__).parents[1] / "bin" / "go2rtc"


def test_rtsp_only_config_has_no_api_exec_encoders_or_camera_backchannel():
    config = json.loads(worker_config("rtsp://user:fake-password@127.0.0.1:9/synthetic"))
    assert config["app"]["modules"] == ["rtsp"]
    assert config["api"]["listen"] == config["webrtc"]["listen"] == ""
    assert config["rtsp"]["listen"] == "127.0.0.1:0"
    assert config["streams"] == {
        "ingress": "rtsp://user:fake-password@127.0.0.1:9/synthetic#backchannel=0",
    }
    assert not ({"preload", "publish", "ffmpeg", "exec"} & config.keys())


@pytest.mark.parametrize("url", ["ffmpeg:camera", "http://127.0.0.1/", "rtsp://", "rtsp://host:0/x",
                                 "rtsp://host/x#backchannel=1", "rtsp://host/{input}",
                                 "rtsp://host/x\r\n", "rtsp://host/" + "x" * 4096],
                         ids=["exec", "http", "missing-host", "zero-port", "directives",
                              "template", "control-byte", "oversize"])
def test_directives_and_noncanonical_sources_are_rejected(url):
    with pytest.raises(ValueError):
        worker_config(url)


@pytest.mark.skipif(sys.platform != "linux", reason="Linux memfd ownership")
def test_private_config_is_sealed_and_cannot_be_modified_or_truncated():
    data = worker_config("rtsp://127.0.0.1:9/synthetic")
    fd = sealed_config(data)
    try:
        assert os.fstat(fd).st_mode & 0o777 == 0o600
        assert os.read(fd, 16384) == data
        with pytest.raises(OSError):
            os.write(fd, b"change")
        with pytest.raises(OSError):
            os.ftruncate(fd, 0)
    finally:
        os.close(fd)


def test_precancelled_owner_never_launches_and_cannot_be_reused(monkeypatch):
    worker = OwnedRtspIngress(CAMERA, "rtsp://127.0.0.1:9/synthetic", binary=BINARY)
    launch = Mock(side_effect=AssertionError("cancelled source launch"))
    monkeypatch.setattr(worker, "_launch", launch)
    assert worker.run(cancelled=lambda: True) == "stopped"
    assert worker.pid is None and worker.restream_url is None
    with pytest.raises(ValueError, match="already used"):
        worker.run(cancelled=lambda: False)
    launch.assert_not_called()


def test_unconfirmed_child_exit_retains_handle_and_hides_restream_url():
    worker = OwnedRtspIngress(CAMERA, "rtsp://127.0.0.1:9/synthetic", binary=BINARY)
    process = Mock()
    process.poll.return_value = None
    process.wait.side_effect = subprocess.TimeoutExpired("synthetic", 2)
    worker._process = process
    worker._url = "rtsp://127.0.0.1:12345/ingress"
    worker.stop()
    process.terminate.assert_not_called()  # signaling is not a teardown receipt
    with pytest.raises(SourceCleanupUnconfirmed):
        worker._retire()
    assert worker._process is process and worker.restream_url is None
    assert process.wait.call_count == 2


def test_cancellation_callback_failure_reaps_owned_child(monkeypatch):
    worker = OwnedRtspIngress(CAMERA, "rtsp://127.0.0.1:9/synthetic", binary=BINARY)
    process = Mock()
    process.poll.return_value = None
    process.wait.return_value = 0

    def launch():
        worker._process = process
        return process

    cancelled = Mock(side_effect=[False, False, RuntimeError("synthetic callback failed")])
    monkeypatch.setattr(worker, "_launch", launch)
    monkeypatch.setattr(worker, "_listener", lambda _process: 12345)
    with pytest.raises(RuntimeError, match="callback failed"):
        worker.run(cancelled=cancelled)
    process.terminate.assert_called_once()
    assert worker.pid is None and worker.restream_url is None


def test_shell_wrappers_are_not_accepted_as_a_direct_process_boundary(tmp_path):
    script = tmp_path / "not-direct-go2rtc"
    script.write_bytes(b"#!/bin/sh\nexit 0\n")
    worker = OwnedRtspIngress(CAMERA, "rtsp://127.0.0.1:9/synthetic", binary=script)
    with pytest.raises(SourceUnavailable, match="direct"):
        worker.run(cancelled=lambda: False)
    assert worker.pid is None


def wait_ready(worker, future):
    deadline = time.monotonic() + 6
    while time.monotonic() < deadline:
        if future.done():
            future.result()  # retain the real failure instead of masking it with readiness timeout
            pytest.fail("worker exited before readiness")
        if worker.restream_url is not None:
            return worker.restream_url
        time.sleep(0.01)
    pytest.fail("owned loopback listener did not become ready")


@pytest.mark.skipif(sys.platform != "linux" or not BINARY.is_file(), reason="pinned Linux go2rtc fixture required")
def test_real_worker_owns_its_listener_and_reaps_it_without_affecting_other_listener(monkeypatch):
    monkeypatch.setenv("CCG_SENTINEL_TOKEN", "must-not-reach-worker")
    worker = OwnedRtspIngress(CAMERA, "rtsp://fake:fake-password@127.0.0.1:9/synthetic", binary=BINARY)
    with socket.socket() as independent, ThreadPoolExecutor(max_workers=1) as pool:
        independent.bind(("127.0.0.1", 0))
        independent.listen()
        future = pool.submit(worker.run, cancelled=lambda: False)
        try:
            url = wait_ready(worker, future)
            port = int(url.split(":")[2].split("/")[0])
            assert port != independent.getsockname()[1]
            pid = worker.pid
            argv = Path(f"/proc/{pid}/cmdline").read_bytes()
            env = Path(f"/proc/{pid}/environ").read_bytes()
            assert b"fake-password" not in argv and b"CCG_SENTINEL_TOKEN" not in env
            assert "fake-password" not in repr(worker)
            with socket.create_connection(("127.0.0.1", port), timeout=2) as consumer:
                worker.stop()
                assert future.result(timeout=6) == "stopped"
                try:
                    assert consumer.recv(1) == b""
                except ConnectionResetError:
                    pass  # a socket still in the accept backlog can be reset on exit
            assert worker.pid is None and worker.restream_url is None
            assert not Path(f"/proc/{pid}").exists()  # reaped, not merely signalled
            with socket.create_connection(independent.getsockname(), timeout=2):
                pass
        finally:
            worker.stop()
            future.result(timeout=6)


@pytest.mark.skipif(sys.platform != "linux" or not BINARY.is_file(), reason="pinned Linux go2rtc fixture required")
def test_real_camera_connection_is_shared_then_closed_before_replacement():
    with CameraFixture() as camera, ThreadPoolExecutor(max_workers=1) as pool:
        worker = OwnedRtspIngress(CAMERA, camera.url, binary=BINARY)
        future = pool.submit(worker.run, cancelled=lambda: False)
        first = second = None
        try:
            url = wait_ready(worker, future)
            assert camera.accepted == 0  # readiness does not open a camera connection
            first, second = describe(url), describe(url)
            assert camera.connected.wait(1)
            assert camera.accepted == camera.peak == 1
            descendants = list(Path(f"/proc/{worker.pid}/task").glob("*/children"))
            assert descendants and all(not item.read_text().strip() for item in descendants)
            worker.stop()
            assert future.result(timeout=6) == "stopped"
            assert camera.closed.wait(2)
            # The next source can connect only after the old owner is reaped.
            with describe(camera.url):
                assert camera.accepted == 2 and camera.peak == 1
        finally:
            worker.stop()
            future.result(timeout=6)
            for consumer in (first, second):
                if consumer is not None:
                    consumer.close()


@pytest.mark.skipif(sys.platform != "linux" or not BINARY.is_file(), reason="pinned Linux go2rtc fixture required")
def test_real_worker_dies_with_its_owner_and_is_reaped_by_isolated_harness():
    root = Path(__file__).parents[1]
    result = subprocess.run([
        sys.executable, str(root / "backend/app/media/exec_guard.py"), str(os.getpid()),
        sys.executable, str(root / "tests/fixtures/check_rtsp_owner_death.py"), "harness", str(BINARY),
    ], capture_output=True, timeout=12, check=True,
        env={"PATH": os.defpath, "PYTHONPATH": str(root)})
    assert len(result.stdout) < 4096
    assert json.loads(result.stdout) == {"worker_killed_on_owner_death": True, "worker_reaped": True}
