"""Explicit owned RTSP preparation and lifespan, isolated from production cameras."""

import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import Mock

import pytest
from _native_runtime_fixture import SyntheticSource
from _owned_runtime_fixture import fake_worker, retired_owned_target
from _rtsp_fixture import CameraFixture, describe

from backend.app.db import source_policy
from backend.app.media.native_runtime import NativeSourceRuntime
from backend.app.media.owned_retirement import RetiredIngress
from backend.app.media.rtsp_ingress import OwnedRtspIngress
from backend.app.media.source_offer import NativeSourceOffer
from backend.app.media.source_registry import SourceCleanupUnconfirmed, SourceUnavailable

BINARY = Path(__file__).resolve().parents[1] / "bin/go2rtc"


def test_idle_owner_closes_without_threads_and_rejects_late_submission():
    camera, runtime, receipt = retired_owned_target()
    worker, entered = fake_worker(camera)
    assert not runtime._executor._threads
    runtime.stop()
    runtime.stop()
    with pytest.raises(SourceUnavailable):
        runtime.activate(receipt, worker, apply_routes=lambda: None)
    assert not entered.is_set() and not runtime._executor._threads


def test_forged_receipt_cannot_launch_or_mutate_durable_intent():
    camera, runtime, receipt = retired_owned_target()
    worker, entered = fake_worker(camera)
    try:
        with pytest.raises(SourceUnavailable):
            runtime.activate(RetiredIngress(receipt.camera_id, receipt.selection), worker, apply_routes=lambda: None)
        assert source_policy.read_for((camera.camera_id,))[camera.camera_id] == receipt.selection
        assert not entered.is_set() and not runtime._executor._threads
    finally:
        runtime.stop()


def test_publish_orders_binding_apply_and_consumer_release_then_shutdown_reaps():
    camera, runtime, receipt = retired_owned_target()
    worker, _ = fake_worker(camera)
    def apply():
        selection = source_policy.read_for((camera.camera_id,))[camera.camera_id]
        assert runtime._bindings.binding(camera.camera_id, selection).worker is worker
        assert runtime._viewers.is_drained(camera.camera_id)
        assert runtime._recordings.is_retired(camera.camera_id)
    running = runtime.activate(receipt, worker, apply_routes=apply)
    assert running.worker is worker and not running.completion.done()
    assert not runtime._viewers.is_suspended(camera.camera_id)
    assert not runtime._recordings.is_suspended(camera.camera_id)
    runtime.stop(timeout=3)
    assert running.completion.result() == "stopped" and worker.pid is None
    assert runtime._viewers.is_drained(camera.camera_id)
    assert runtime._recordings.is_retired(camera.camera_id)
    assert not runtime._runs and all(not t.is_alive() for t in runtime._executor._threads)
    assert source_policy.read_for((camera.camera_id,))[camera.camera_id].phase == "blocked"


@pytest.mark.parametrize("failure", ["readiness", "publication"])
def test_failed_preparation_is_retained_and_never_releases_consumers(failure):
    camera, runtime, receipt = retired_owned_target()
    worker, entered = fake_worker(camera, ready=failure != "readiness")
    def apply():
        raise ValueError("synthetic publication failure")
    try:
        with pytest.raises((SourceUnavailable, ValueError)):
            runtime.activate(receipt, worker, apply_routes=apply, ready_timeout=0.03)
        assert entered.is_set() and worker.pid is None and runtime._runs
        assert runtime._viewers.is_drained(camera.camera_id)
        assert runtime._recordings.is_retired(camera.camera_id)
    finally:
        runtime.stop(timeout=3)


def test_shutdown_during_publication_cannot_reopen_consumers():
    camera, runtime, receipt = retired_owned_target()
    worker, _ = fake_worker(camera)
    entered, release = threading.Event(), threading.Event()
    def apply():
        entered.set()
        assert release.wait(2)
    with ThreadPoolExecutor(max_workers=1) as coordinator:
        preparing = coordinator.submit(runtime.activate, receipt, worker, apply_routes=apply)
        try:
            assert entered.wait(1)
            with pytest.raises(SourceCleanupUnconfirmed):
                runtime.stop(timeout=0.02)
            assert runtime._runs and not runtime._stopped
        finally:
            release.set()
        with pytest.raises(SourceUnavailable):
            preparing.result(timeout=2)
    runtime.stop(timeout=3)
    assert runtime._viewers.is_drained(camera.camera_id) and runtime._stopped


def test_shutdown_while_claim_waits_cannot_launch_a_late_worker():
    camera, runtime, receipt = retired_owned_target()
    worker, entered = fake_worker(camera)
    admitted = threading.Event()
    original_claim = runtime.retirement.claim
    def claim(value):
        admitted.set()
        return original_claim(value)
    runtime.retirement.claim = claim
    with ThreadPoolExecutor(max_workers=1) as coordinator:
        with runtime._bindings.reconciliation.exclusive():
            preparing = coordinator.submit(runtime.activate, receipt, worker, apply_routes=lambda: None)
            assert admitted.wait(1)
            with pytest.raises(SourceCleanupUnconfirmed):
                runtime.stop(timeout=0.02)
        with pytest.raises(SourceUnavailable):
            preparing.result(timeout=2)
    runtime.stop(timeout=3)
    assert not entered.is_set() and not runtime._executor._threads and runtime._stopped


def test_old_owner_shutdown_does_not_hold_a_new_native_generation():
    camera, runtime, receipt = retired_owned_target()
    worker, _ = fake_worker(camera)
    running = runtime.activate(receipt, worker, apply_routes=lambda: None)
    selected = source_policy.read_for((camera.camera_id,))[camera.camera_id]
    retired = runtime.retirement.retire(running, target_mode="native", expected_revision=selected.revision)
    native = NativeSourceRuntime(viewers=runtime._viewers)
    source = SyntheticSource()
    try:
        session = native.activate(retirement=runtime.retirement, receipt=retired,
                                  offer=NativeSourceOffer(camera.camera_id, "test/owned-to-native", lambda: source, lambda: True),
                                  apply_routes=lambda: None)
        runtime._runs[camera.camera_id]._ended(running.completion)  # delayed stale callback
        runtime.stop(timeout=3)
        assert not runtime._viewers.is_suspended(camera.camera_id)
        assert source_policy.read_for((camera.camera_id,))[camera.camera_id] == session.selection
        assert not session.completion.done()
    finally:
        native.stop(timeout=3)
        runtime.stop(timeout=3)


def test_one_uncertain_child_does_not_skip_another_owner():
    camera, runtime, receipt = retired_owned_target()
    other, _, other_receipt = retired_owned_target(runtime=runtime, mac="aa:bb:cc:dd:ee:04")
    failed, _ = fake_worker(camera, cleanup_error=True)
    healthy, _ = fake_worker(other)
    runtime.activate(receipt, failed, apply_routes=lambda: None)
    runtime.activate(other_receipt, healthy, apply_routes=lambda: None)
    with pytest.raises(SourceCleanupUnconfirmed):
        runtime.stop(timeout=3)
    assert failed.pid is not None and healthy.pid is None and runtime._runs
    assert all(not t.is_alive() for t in runtime._executor._threads)


def test_stale_worker_cannot_withdraw_or_discard_a_new_binding():
    camera, runtime, receipt = retired_owned_target()
    worker, _ = fake_worker(camera)
    stale, _ = fake_worker(camera)
    try:
        runtime.activate(receipt, worker, apply_routes=lambda: None)
        assert not runtime._bindings.withdraw(camera.camera_id, worker=stale)
        runtime._bindings.discard(camera.camera_id, worker=stale)
        selected = source_policy.read_for((camera.camera_id,))[camera.camera_id]
        assert runtime._bindings.binding(camera.camera_id, selected).worker is worker
    finally:
        runtime.stop(timeout=3)


@pytest.mark.skipif(sys.platform != "linux" or not BINARY.is_file(), reason="pinned Linux go2rtc fixture unavailable")
def test_real_owned_worker_is_retained_shared_and_reaped_without_camera_hardware():
    camera, runtime, receipt = retired_owned_target()
    with CameraFixture() as fixture:
        worker = OwnedRtspIngress(camera.camera_id, fixture.url, binary=BINARY)
        try:
            running = runtime.activate(receipt, worker, apply_routes=Mock())
            with describe(worker.restream_url), describe(worker.restream_url):
                assert fixture.accepted == 1
                runtime.stop(timeout=10)
                assert fixture.closed.wait(1)
        finally:
            runtime.stop(timeout=10)
        assert worker.pid is None and running.completion.done()
