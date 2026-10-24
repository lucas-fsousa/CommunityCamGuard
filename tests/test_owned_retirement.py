"""Retirement coordinator: failure latching and metadata-only loopback receipt."""

import sys
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path
from unittest.mock import Mock

import pytest
from _rtsp_fixture import CameraFixture, describe

from backend.app.db import registry, source_policy
from backend.app.media.owned_bindings import OwnedIngressBindings
from backend.app.media.owned_retirement import OwnedRetirementCoordinator, OwnedRun
from backend.app.media.rtsp_ingress import OwnedRtspIngress
from backend.app.media.source_registry import SourceCleanupUnconfirmed, SourceUnavailable
from backend.app.media.viewer_gate import ViewerGate
from backend.app.recording.ingress_gate import RecordingIngressGate

BINARY = Path(__file__).parents[1] / "bin/go2rtc"


@pytest.fixture
def env():
    registry.init_db()
    camera = registry.upsert_camera("aa:bb:cc:dd:ee:03", last_ip="192.0.2.3", stream_path="/synthetic")
    other = registry.upsert_camera("aa:bb:cc:dd:ee:04")
    pending = source_policy.begin_change(camera.camera_id, "owned_rtsp", expected_revision=0)
    selection = source_policy.activate(camera.camera_id, expected_revision=pending.revision)
    bindings, viewers = OwnedIngressBindings(), ViewerGate()
    recordings = RecordingIngressGate(lock=threading.Lock(), retire=Mock(), has_process=lambda _: False)
    coordinator = OwnedRetirementCoordinator(viewers=viewers, recordings=recordings, bindings=bindings)
    return camera, other, selection, bindings, viewers, recordings, coordinator


def simulated_run(camera, bindings):
    worker = OwnedRtspIngress(camera.camera_id, camera.rtsp_url, binary=BINARY)
    worker._process = Mock(pid=12345)
    worker._url = "rtsp://127.0.0.1:12345/ingress_" + "a" * 32
    bindings.attach(worker)
    completion = Mock(spec=Future)
    def finished(**kwargs):
        worker._process = None  # simulated normal run() finally/wait/reap
        worker._url = None
        return "stopped"
    completion.result.side_effect = finished
    return OwnedRun(worker, completion)


def test_success_leaves_transition_and_consumer_holds_not_an_activation(env):
    camera, other, selected, bindings, viewers, recordings, coordinator = env
    running = simulated_run(camera, bindings)
    receipt = coordinator.retire(running, target_mode="native", expected_revision=selected.revision)
    assert receipt.camera_id == camera.camera_id
    assert receipt.selection.mode == "native" and receipt.selection.phase == "transition"
    assert receipt.selection.revision == selected.revision + 1
    assert source_policy.read_for((camera.camera_id,))[camera.camera_id] == receipt.selection
    assert source_policy.read_for((other.camera_id,))[other.camera_id].permits_legacy_rtsp
    assert viewers.is_suspended(camera.camera_id) and recordings.is_suspended(camera.camera_id)
    assert not viewers.is_suspended(other.camera_id)
    running.completion.result.assert_called_once_with(timeout=6)


@pytest.mark.parametrize("failure", ["viewer", "recorder", "wait", "unreaped", "discard", "persist_block"])
def test_failure_never_returns_receipt_and_latches_persistent_or_local_block(env, failure, monkeypatch):
    camera, _other, selected, bindings, viewers, recordings, coordinator = env
    running = simulated_run(camera, bindings)
    if failure == "viewer":
        viewers.admit(camera.camera_id)  # deliberately uncooperative consumer
    elif failure == "recorder":
        recordings._retire = Mock(side_effect=OSError("synthetic cleanup failure"))
    elif failure == "wait":
        running.completion.result.side_effect = TimeoutError("synthetic unconfirmed wait")
    elif failure == "unreaped":
        running.completion.result.side_effect = None
        running.completion.result.return_value = "stopped"
    elif failure == "discard":
        monkeypatch.setattr(bindings, "discard", Mock(side_effect=OSError("synthetic binding failure")))
    else:
        running.completion.result.side_effect = TimeoutError("synthetic unconfirmed wait")
        monkeypatch.setattr(source_policy, "block", Mock(side_effect=OSError("synthetic database failure")))
    with pytest.raises(SourceCleanupUnconfirmed):
        coordinator.retire(running, target_mode="native", expected_revision=selected.revision, drain_timeout=0.01)
    state = source_policy.read_for((camera.camera_id,))[camera.camera_id]
    assert state.phase == ("transition" if failure == "persist_block" else "blocked")
    assert coordinator._failed is running
    assert viewers.is_suspended(camera.camera_id)
    with pytest.raises(SourceCleanupUnconfirmed):
        coordinator.retire(running, target_mode="owned_rtsp", expected_revision=state.revision)


def test_stale_revision_or_busy_operation_performs_no_drain_or_stop(env):
    camera, _other, selected, bindings, viewers, _recordings, coordinator = env
    running = simulated_run(camera, bindings)
    with pytest.raises(source_policy.SourcePolicyConflict):
        coordinator.retire(running, target_mode="native", expected_revision=0)
    coordinator._operation.acquire()
    try:
        with pytest.raises(SourceUnavailable):
            coordinator.retire(running, target_mode="native", expected_revision=selected.revision)
    finally:
        coordinator._operation.release()
    assert not viewers.is_suspended(camera.camera_id) and not running.worker._stop.is_set()
    running.completion.result.assert_not_called()


def test_shared_legacy_ingress_is_rejected_before_policy_or_process_changes(env):
    camera, other, _selected, _bindings, _viewers, _recordings, coordinator = env
    worker = OwnedRtspIngress(other.camera_id, camera.rtsp_url, binary=BINARY)
    running = OwnedRun(worker, Mock(spec=Future))
    with pytest.raises(SourceUnavailable, match="matching owned"):
        coordinator.retire(running, target_mode="native", expected_revision=0)
    assert source_policy.read_for((other.camera_id,))[other.camera_id].permits_legacy_rtsp
    assert not worker._stop.is_set()
    running.completion.result.assert_not_called()


@pytest.mark.parametrize("timeout", [0, float("inf"), True, 31])
def test_invalid_budget_is_rejected_before_persisting_transition(env, timeout):
    camera, _other, selected, bindings, _viewers, _recordings, coordinator = env
    running = simulated_run(camera, bindings)
    with pytest.raises(ValueError):
        coordinator.retire(running, target_mode="native", expected_revision=selected.revision, drain_timeout=timeout)
    assert source_policy.read_for((camera.camera_id,))[camera.camera_id] == selected


@pytest.mark.skipif(sys.platform != "linux" or not BINARY.is_file(), reason="pinned Linux go2rtc fixture required")
def test_real_coordinator_reaps_owned_ingress_before_replacement_can_connect(env):
    camera, _other, selected, bindings, viewers, recordings, coordinator = env
    with CameraFixture() as fixture, ThreadPoolExecutor(max_workers=1) as pool:
        worker = OwnedRtspIngress(camera.camera_id, fixture.url, binary=BINARY)
        completion = pool.submit(worker.run, cancelled=lambda: False)
        consumer = None
        try:
            deadline = time.monotonic() + 6
            while worker.restream_url is None and not completion.done() and time.monotonic() < deadline:
                time.sleep(0.01)
            if completion.done():
                completion.result()
            bindings.attach(worker)
            consumer = describe(worker.restream_url)
            assert fixture.accepted == fixture.peak == 1
            receipt = coordinator.retire(OwnedRun(worker, completion), target_mode="native",
                                         expected_revision=selected.revision)
            assert receipt.selection.phase == "transition"
            assert worker.pid is None and fixture.closed.wait(2)
            assert viewers.is_suspended(camera.camera_id) and recordings.is_suspended(camera.camera_id)
            with describe(fixture.url):
                assert fixture.accepted == 2 and fixture.peak == 1
        finally:
            worker.stop()
            completion.result(timeout=6)
            if consumer is not None:
                consumer.close()
