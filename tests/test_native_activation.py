"""Retirement -> native readiness -> publication; synthetic sources only."""

import sys
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock

import pytest
from _native_fixture import FRAGMENT, INIT
from _rtsp_fixture import CameraFixture, describe
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.app import auth
from backend.app.api import media
from backend.app.db import registry, source_policy
from backend.app.media.fragmented_mp4 import Mp4Chunk
from backend.app.media.go2rtc import Go2rtc
from backend.app.media.mp4_fanout import Mp4Fanout
from backend.app.media.native_activation import NativeActivation
from backend.app.media.owned_bindings import OwnedIngressBindings
from backend.app.media.owned_retirement import OwnedRetirementCoordinator, OwnedRun
from backend.app.media.prepared_native import PreparedNative
from backend.app.media.rtsp_ingress import OwnedRtspIngress
from backend.app.media.source_offer import NativeSourceOffer
from backend.app.media.source_registry import (
    SourceCleanupUnconfirmed,
    SourceRegistry,
    SourceUnavailable,
)
from backend.app.media.viewer_gate import ViewerGate
from backend.app.recording.ingress_gate import RecordingIngressGate


class SyntheticSource:
    def __init__(self, *, ready=True, invalid=False, cleanup_error=False):
        self.hub = Mp4Fanout()
        if ready:
            self.hub.publish(Mp4Chunk(True, b"invalid MP4" if invalid else INIT))
            self.hub.publish(Mp4Chunk(False, FRAGMENT))
        self.finish = threading.Event()
        self.entered = threading.Event()
        self.closed = threading.Event()
        self.cleanup_error = cleanup_error

    def run(self, *, cancelled):
        self.entered.set()
        try:
            while not self.finish.wait(0.002):
                if cancelled():
                    return "stopped"
        finally:
            self.hub.close()
            self.closed.set()
            if self.cleanup_error:
                raise SourceCleanupUnconfirmed("synthetic cleanup uncertainty")

    def subscribe(self):
        return self.hub.subscribe()


@pytest.fixture
def retired():
    registry.init_db()
    camera = registry.upsert_camera("aa:bb:cc:dd:ee:03")
    pending = source_policy.begin_change(camera.camera_id, "owned_rtsp", expected_revision=0)
    selected = source_policy.activate(camera.camera_id, expected_revision=pending.revision)
    bindings, viewers = OwnedIngressBindings(), ViewerGate()
    recordings = RecordingIngressGate(lock=threading.Lock(), retire=lambda _: None, has_process=lambda _: False)
    coordinator = OwnedRetirementCoordinator(viewers=viewers, recordings=recordings, bindings=bindings)
    old = OwnedRtspIngress(camera.camera_id, "rtsp://127.0.0.1:9/synthetic", binary=Path(__file__))
    old._process = Mock(pid=12345)
    old._url = "rtsp://127.0.0.1:12345/ingress_" + "a" * 32
    bindings.attach(old)
    done = Mock(spec=Future)
    done.result.side_effect = lambda **kwargs: setattr(old, "_process", None)
    receipt = coordinator.retire(OwnedRun(old, done), target_mode="native", expected_revision=selected.revision)
    return camera, coordinator, receipt, viewers, recordings


def test_prepares_once_then_publishes_only_ready_native_video(retired):
    camera, retirement, receipt, viewers, recordings = retired
    source, sources = SyntheticSource(), SourceRegistry()
    factory = Mock(return_value=source)
    offer = NativeSourceOffer(camera.camera_id, "test/shared", factory, lambda: True)

    def publish():
        assert viewers.is_drained(camera.camera_id) and recordings.is_retired(camera.camera_id)
        assert source.entered.is_set() and source.hub.reader_count == 0

    with ThreadPoolExecutor(max_workers=1) as pool:
        activation = NativeActivation(retirement=retirement, sources=sources, executor=pool, apply_routes=publish)
        session = activation.start(receipt, offer)
        try:
            assert factory.call_count == 1 and session.selection.phase == "active"
            assert not viewers.is_suspended(camera.camera_id) and recordings.is_retired(camera.camera_id)
            with pytest.raises(SourceUnavailable):
                activation.start(receipt, offer)
            with pytest.raises(SourceUnavailable), retirement.claim(receipt):
                raise AssertionError("retirement receipt replayed")
            first, second = sources.subscribe(camera.camera_id), sources.subscribe(camera.camera_id)
            try:
                assert first.take().data == second.take().data == INIT
                assert first.take().data == second.take().data == FRAGMENT
            finally:
                first.close(); second.close()
        finally:
            session.close()
        assert source.closed.is_set() and sources.state(camera.camera_id) == "idle"
        assert viewers.is_suspended(camera.camera_id)


@pytest.mark.parametrize("failure", ["no-output", "invalid-output", "publish", "cleanup"])
def test_activation_failure_blocks_without_releasing_recording_or_viewers(retired, failure):
    camera, retirement, receipt, viewers, recordings = retired
    source = SyntheticSource(ready=failure != "no-output", invalid=failure == "invalid-output",
                             cleanup_error=failure == "cleanup")
    sources = SourceRegistry()
    offer = NativeSourceOffer(camera.camera_id, "test/shared", lambda: source, lambda: True)
    def publish():
        if failure in {"publish", "cleanup"}:
            raise OSError("synthetic route publication failed")
    with ThreadPoolExecutor(max_workers=1) as pool:
        activation = NativeActivation(retirement=retirement, sources=sources, executor=pool, apply_routes=publish)
        with pytest.raises((SourceUnavailable, ValueError, OSError)):
            activation.start(receipt, offer, ready_timeout=0.05)
        assert activation.session is not None and source.closed.is_set()
        assert source_policy.read_for((camera.camera_id,))[camera.camera_id].phase == "blocked"
        assert viewers.is_suspended(camera.camera_id) and recordings.is_retired(camera.camera_id)
        assert sources.state(camera.camera_id) == ("blocked" if failure == "cleanup" else "idle")


@pytest.mark.parametrize("preflight", ["forged", "wrong-camera", "revoked", "lost-hold"])
def test_preflight_never_constructs_native_source(retired, preflight):
    camera, retirement, receipt, viewers, _recordings = retired
    factory = Mock(side_effect=AssertionError("unauthorized source factory"))
    offer = NativeSourceOffer(camera.camera_id, "test/shared", factory, lambda: preflight != "revoked")
    if preflight == "forged":
        receipt = replace(receipt)  # same data is not the coordinator-issued object
    elif preflight == "wrong-camera":
        offer = replace(offer, camera_id="cam_" + "f" * 24)
    elif preflight == "lost-hold":
        viewers.resume(camera.camera_id)
    with ThreadPoolExecutor(max_workers=1) as pool:
        activation = NativeActivation(retirement=retirement, sources=SourceRegistry(), executor=pool,
                                      apply_routes=lambda: None)
        with pytest.raises(SourceUnavailable):
            activation.start(receipt, offer)
    factory.assert_not_called()


def test_natural_source_end_recloses_gate_and_blocks_policy(retired):
    camera, retirement, receipt, viewers, _recordings = retired
    source, sources = SyntheticSource(), SourceRegistry()
    offer = NativeSourceOffer(camera.camera_id, "test/shared", lambda: source, lambda: True)
    with ThreadPoolExecutor(max_workers=1) as pool:
        activation = NativeActivation(retirement=retirement, sources=sources, executor=pool, apply_routes=lambda: None)
        session = activation.start(receipt, offer)
        source.finish.set()
        session.completion.result(timeout=2)
        deadline = time.monotonic() + 2
        while not viewers.is_suspended(camera.camera_id) and time.monotonic() < deadline:
            time.sleep(0.005)
        assert viewers.is_suspended(camera.camera_id)
        # Callback's policy write follows the hold; synchronize through close.
        session.close()
        assert session.selection.phase == "blocked"


def test_evidence_guard_is_rate_limited_not_per_frame(retired):
    camera, _retirement, receipt, viewers, _recordings = retired
    now = [0.0]
    valid = Mock(return_value=True)
    offer = NativeSourceOffer(camera.camera_id, "test/shared", Mock(), valid)
    session = PreparedNative(offer=offer, selection=receipt.selection, sources=SourceRegistry(),
                             viewers=viewers, clock=lambda: now[0])
    for _ in range(1000):
        assert not session.cancelled()
    assert valid.call_count == 1
    now[0] = 1
    valid.return_value = False
    assert session.cancelled() and valid.call_count == 2
    assert session.cancelled() and valid.call_count == 2


@pytest.mark.parametrize("collision", ["camera", "domain"])
def test_failed_admission_never_borrows_or_stops_an_existing_generation(retired, collision):
    camera, retirement, receipt, _viewers, _recordings = retired
    sources, existing = SourceRegistry(capacity=2), SyntheticSource()
    existing_id = camera.camera_id if collision == "camera" else "cam_" + "e" * 24
    factory = Mock(side_effect=AssertionError("duplicate source factory"))
    offer = NativeSourceOffer(camera.camera_id, "test/shared", factory, lambda: True)
    publish = Mock()
    with ThreadPoolExecutor(max_workers=2) as pool:
        old = pool.submit(sources.run, existing_id, domain="test/shared", factory=lambda: existing,
                          cancelled=lambda: False)
        assert existing.entered.wait(1)
        try:
            activation = NativeActivation(retirement=retirement, sources=sources, executor=pool, apply_routes=publish)
            with pytest.raises(SourceUnavailable):
                activation.start(receipt, offer, ready_timeout=0.1)
            factory.assert_not_called()
            publish.assert_not_called()
            assert not existing.closed.is_set() and sources.state(existing_id) == "running"
        finally:
            existing.finish.set()
            old.result(timeout=2)


@pytest.mark.parametrize("during_publish", ["evidence-revoked", "source-ended"])
def test_publication_races_cannot_reopen_viewers_for_a_failed_source(retired, during_publish):
    camera, retirement, receipt, viewers, _recordings = retired
    source, sources, valid = SyntheticSource(), SourceRegistry(), [True]
    offer = NativeSourceOffer(camera.camera_id, "test/shared", lambda: source, lambda: valid[0])
    def publish():
        if during_publish == "evidence-revoked":
            valid[0] = False
        else:
            source.finish.set()
            activation.session.completion.result(timeout=2)
    with ThreadPoolExecutor(max_workers=1) as pool:
        activation = NativeActivation(retirement=retirement, sources=sources, executor=pool, apply_routes=publish)
        with pytest.raises(SourceUnavailable):
            activation.start(receipt, offer)
        assert viewers.is_drained(camera.camera_id) and activation.session.selection.phase == "blocked"


def test_activated_source_reaches_existing_dashboard_socket_without_legacy_connection(retired, monkeypatch):
    camera, retirement, receipt, viewers, recordings = retired
    source, sources = SyntheticSource(), SourceRegistry()
    factory = Mock(return_value=source)
    offer = NativeSourceOffer(camera.camera_id, "test/shared", factory, lambda: True)
    app = FastAPI()
    app.include_router(media.router)
    app.state.native_sources, app.state.viewer_sessions = sources, viewers
    monkeypatch.setattr(media.websockets, "connect", lambda *a, **k: pytest.fail("legacy connection opened"))
    with ThreadPoolExecutor(max_workers=1) as pool:
        activation = NativeActivation(retirement=retirement, sources=sources, executor=pool, apply_routes=lambda: None)
        session = activation.start(receipt, offer)
        try:
            with TestClient(app) as client:
                client.cookies.set(auth.COOKIE_NAME, auth.issue_token())
                with client.websocket_connect(f"/api/go2rtc/ws?src={camera.camera_id}_hd") as socket:
                    socket.send_text('{"type":"mse","value":"avc1.640029"}')
                    assert socket.receive_json() == {"type": "mse", "value": 'video/mp4; codecs="avc1.42C01E"'}
                    assert socket.receive_bytes() == INIT and socket.receive_bytes() == FRAGMENT
            assert factory.call_count == 1 and recordings.is_retired(camera.camera_id)
        finally:
            session.close()


@pytest.mark.skipif(sys.platform != "linux" or not (Path(__file__).parents[1] / "bin/go2rtc").is_file(),
                    reason="pinned Linux go2rtc fixture required")
def test_real_owned_retirement_precedes_native_factory_and_publication():
    registry.init_db()
    camera = registry.upsert_camera("aa:bb:cc:dd:ee:03")
    pending = source_policy.begin_change(camera.camera_id, "owned_rtsp", expected_revision=0)
    selected = source_policy.activate(camera.camera_id, expected_revision=pending.revision)
    bindings, viewers = OwnedIngressBindings(), ViewerGate()
    recordings = RecordingIngressGate(lock=threading.Lock(), retire=lambda _: None, has_process=lambda _: False)
    retirement = OwnedRetirementCoordinator(viewers=viewers, recordings=recordings, bindings=bindings)
    source, sources = SyntheticSource(), SourceRegistry()
    with CameraFixture() as fixture, ThreadPoolExecutor(max_workers=1) as pool:
        old = OwnedRtspIngress(camera.camera_id, fixture.url, binary=Path(__file__).parents[1] / "bin/go2rtc")
        completion = pool.submit(old.run, cancelled=lambda: False)
        consumer = session = None
        try:
            deadline = time.monotonic() + 6
            while old.restream_url is None and not completion.done() and time.monotonic() < deadline:
                time.sleep(0.01)
            if completion.done():
                completion.result()
            bindings.attach(old)
            consumer = describe(old.restream_url)
            receipt = retirement.retire(OwnedRun(old, completion), target_mode="native",
                                         expected_revision=selected.revision)
            def factory():
                assert old.pid is None and fixture.closed.wait(2)
                assert fixture.accepted == fixture.peak == 1
                return source
            offer = NativeSourceOffer(camera.camera_id, "test/shared", factory, lambda: True)
            activation = NativeActivation(retirement=retirement, sources=sources, executor=pool,
                                          apply_routes=lambda: None)
            session = activation.start(receipt, offer)
            assert session.selection.phase == "active" and not viewers.is_suspended(camera.camera_id)
            assert recordings.is_retired(camera.camera_id)
            assert fixture.accepted == 1  # new source is synthetic, no replacement camera I/O
        finally:
            if session is not None:
                session.close()
            source.finish.set()
            old.stop()
            completion.result(timeout=6)
            if consumer is not None:
                consumer.close()


def test_close_cannot_report_success_while_source_submission_has_no_tracked_handle(retired):
    camera, _retirement, receipt, viewers, _recordings = retired
    entered, release, closing = threading.Event(), threading.Event(), threading.Event()
    complete = Future()
    complete.set_result("stopped")
    class DelayedSubmission:
        def submit(self, *args, **kwargs):
            entered.set()
            assert release.wait(2)
            return complete
    offer = NativeSourceOffer(camera.camera_id, "test/shared", Mock(), lambda: True)
    session = PreparedNative(offer=offer, selection=receipt.selection, sources=SourceRegistry(), viewers=viewers)
    def close():
        closing.set()
        session.close()
    with ThreadPoolExecutor(max_workers=2) as pool:
        launch = pool.submit(session.launch, DelayedSubmission())
        assert entered.wait(1)
        stop = pool.submit(close)
        try:
            assert closing.wait(1)
            with pytest.raises(TimeoutError):
                stop.result(timeout=0.02)
        finally:
            release.set()
        launch.result(timeout=2)
        stop.result(timeout=2)
    assert session.completion is complete


def test_closed_preparation_can_never_submit_a_late_source_job(retired):
    camera, _retirement, receipt, viewers, _recordings = retired
    offer = NativeSourceOffer(camera.camera_id, "test/shared", Mock(), lambda: True)
    session = PreparedNative(offer=offer, selection=receipt.selection, sources=SourceRegistry(), viewers=viewers)
    session.close()
    viewers.resume(camera.camera_id)
    replacement_viewer = viewers.admit(camera.camera_id)
    try:
        session.close()  # stale confirmed close must not cancel a later generation
        assert not viewers.is_suspended(camera.camera_id) and not replacement_viewer.cancelled.is_set()
    finally:
        viewers.release(replacement_viewer)
    executor = Mock()
    with pytest.raises(SourceUnavailable):
        session.launch(executor)
    executor.submit.assert_not_called()


def test_shared_hub_guard_covers_preparation_and_reentrant_route_publication(retired, tmp_path):
    camera, retirement, receipt, _viewers, _recordings = retired
    source, sources = SyntheticSource(ready=False), SourceRegistry()
    engine = Go2rtc(manage=False, config_path=tmp_path / "hub.json")
    engine.reconciliation = retirement.reconciliation
    engine.reload_external = Mock(return_value=True)
    offered = NativeSourceOffer(camera.camera_id, "test/shared", lambda: source, lambda: True)
    events, attempting = [], threading.Event()
    def publish():
        events.append("publish")
        engine.write_config([])  # same coordinator thread re-enters the shared guard
    def reconcile():
        attempting.set()
        engine.restart([])
        events.append("reconcile")
    with ThreadPoolExecutor(max_workers=1) as source_pool, ThreadPoolExecutor(max_workers=2) as coordinator_pool:
        activation = NativeActivation(retirement=retirement, sources=sources, executor=source_pool, apply_routes=publish)
        preparing = coordinator_pool.submit(activation.start, receipt, offered, ready_timeout=2)
        session = None
        try:
            assert source.entered.wait(1)
            applying = coordinator_pool.submit(reconcile)
            assert attempting.wait(1)
            with pytest.raises(TimeoutError):
                applying.result(timeout=0.02)
            source.hub.publish(Mp4Chunk(True, INIT))
            source.hub.publish(Mp4Chunk(False, FRAGMENT))
            session = preparing.result(timeout=3)
            applying.result(timeout=2)
            assert events == ["publish", "reconcile"]
        finally:
            source.finish.set()
            if session is not None:
                session.close()
