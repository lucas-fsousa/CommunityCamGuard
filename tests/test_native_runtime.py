"""Server ownership and shutdown of explicit native activations, no camera I/O."""

import threading
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import Mock

import pytest
from _native_runtime_fixture import SyntheticSource, retired_native_transition

from backend.app.media.native_runtime import NativeSourceRuntime
from backend.app.media.source_offer import NativeSourceOffer
from backend.app.media.source_registry import SourceCleanupUnconfirmed, SourceUnavailable


def test_runtime_is_idle_at_boot_and_rejects_admission_after_stop():
    camera, retirement, receipt, viewers, _recordings = retired_native_transition()
    runtime = NativeSourceRuntime(viewers=viewers)
    assert runtime.sources.camera_ids() == () and not runtime._executor._threads
    runtime.stop()
    runtime.stop()
    factory = Mock()
    with pytest.raises(SourceUnavailable):
        runtime.activate(retirement=retirement, receipt=receipt,
                         offer=NativeSourceOffer(camera.camera_id, "test/one", factory, lambda: True),
                         apply_routes=lambda: None)
    factory.assert_not_called()
    assert not runtime._executor._threads


def test_runtime_owns_the_run_and_reaps_threads_on_shutdown():
    camera, retirement, receipt, viewers, recordings = retired_native_transition()
    runtime, source = NativeSourceRuntime(viewers=viewers), SyntheticSource()
    session = runtime.activate(retirement=retirement, receipt=receipt,
                               offer=NativeSourceOffer(camera.camera_id, "test/one", lambda: source, lambda: True),
                               apply_routes=lambda: None)
    assert session.completion is not None and runtime.sources.state(camera.camera_id) == "running"
    runtime.stop(timeout=3)
    assert source.closed.is_set() and session._closed
    assert runtime._stopped and not runtime._activations and not runtime._executor._jobs
    assert all(not thread.is_alive() for thread in runtime._executor._threads)
    assert viewers.is_drained(camera.camera_id) and recordings.is_retired(camera.camera_id)


def test_shutdown_cancels_inflight_preparation_without_publishing():
    camera, retirement, receipt, viewers, _recordings = retired_native_transition()
    runtime, source, publish = NativeSourceRuntime(viewers=viewers), SyntheticSource(ready=False), Mock()
    with ThreadPoolExecutor(max_workers=1) as coordinator:
        preparing = coordinator.submit(runtime.activate, retirement=retirement, receipt=receipt,
                                        offer=NativeSourceOffer(camera.camera_id, "test/one", lambda: source, lambda: True),
                                        apply_routes=publish)
        assert source.entered.wait(1)
        runtime.stop(timeout=3)
        with pytest.raises(SourceUnavailable):
            preparing.result(timeout=2)
    publish.assert_not_called()
    assert source.closed.is_set() and runtime._stopped


def test_shutdown_timeout_retains_handles_and_retry_can_confirm_late_cleanup():
    camera, retirement, receipt, viewers, _recordings = retired_native_transition()
    runtime, source = NativeSourceRuntime(viewers=viewers), SyntheticSource()
    publishing, release = threading.Event(), threading.Event()
    def publish():
        publishing.set()
        assert release.wait(2)
    with ThreadPoolExecutor(max_workers=1) as coordinator:
        preparing = coordinator.submit(runtime.activate, retirement=retirement, receipt=receipt,
                                        offer=NativeSourceOffer(camera.camera_id, "test/one", lambda: source, lambda: True),
                                        apply_routes=publish)
        try:
            assert publishing.wait(1)
            with pytest.raises(SourceCleanupUnconfirmed):
                runtime.stop(timeout=0.02)
            assert runtime._activations and not runtime._stopped
        finally:
            release.set()
        with pytest.raises(SourceUnavailable):
            preparing.result(timeout=2)
    runtime.stop(timeout=3)
    assert runtime._stopped and viewers.is_drained(camera.camera_id)


def test_one_uncertain_source_does_not_skip_other_source_cleanup():
    camera, retirement, receipt, viewers, _recordings = retired_native_transition()
    other, other_retirement, other_receipt, _, _ = retired_native_transition(mac="aa:bb:cc:dd:ee:04", viewers=viewers)
    runtime = NativeSourceRuntime(viewers=viewers, capacity=2)
    failed, healthy = SyntheticSource(cleanup_error=True), SyntheticSource()
    runtime.activate(retirement=retirement, receipt=receipt,
                     offer=NativeSourceOffer(camera.camera_id, "test/one", lambda: failed, lambda: True),
                     apply_routes=lambda: None)
    good_session = runtime.activate(retirement=other_retirement, receipt=other_receipt,
                                    offer=NativeSourceOffer(other.camera_id, "test/two", lambda: healthy, lambda: True),
                                    apply_routes=lambda: None)
    with pytest.raises(SourceCleanupUnconfirmed):
        runtime.stop(timeout=3)
    assert failed.closed.is_set() and healthy.closed.is_set() and good_session._closed
    assert runtime.sources.state(camera.camera_id) == "blocked"
    assert runtime._activations and not runtime._stopped
    assert all(not thread.is_alive() for thread in runtime._executor._threads)


def test_shutdown_before_session_exists_cannot_submit_a_late_source():
    camera, retirement, receipt, viewers, _recordings = retired_native_transition()
    runtime = NativeSourceRuntime(viewers=viewers)
    validating, release = threading.Event(), threading.Event()
    factory = Mock()
    def valid():
        validating.set()
        assert release.wait(2)
        return True
    offer = NativeSourceOffer(camera.camera_id, "test/one", factory, valid)
    with ThreadPoolExecutor(max_workers=1) as coordinator:
        preparing = coordinator.submit(runtime.activate, retirement=retirement, receipt=receipt,
                                        offer=offer, apply_routes=lambda: None)
        try:
            assert validating.wait(1)
            with pytest.raises(SourceCleanupUnconfirmed):
                runtime.stop(timeout=0.02)
            assert runtime._activations[camera.camera_id].session is None
        finally:
            release.set()
        with pytest.raises(SourceUnavailable):
            preparing.result(timeout=2)
    runtime.stop(timeout=3)
    factory.assert_not_called()
    assert not runtime._executor._threads and runtime._stopped


def test_active_uncooperative_source_cannot_turn_short_shutdown_budget_into_a_long_wait():
    camera, retirement, receipt, viewers, _recordings = retired_native_transition()
    runtime, source = NativeSourceRuntime(viewers=viewers), SyntheticSource(ignore_cancel=True)
    session = runtime.activate(retirement=retirement, receipt=receipt,
                               offer=NativeSourceOffer(camera.camera_id, "test/one", lambda: source, lambda: True),
                               apply_routes=lambda: None)
    with ThreadPoolExecutor(max_workers=1) as shutdown:
        try:
            closing = shutdown.submit(runtime.stop, timeout=0.03)
            with pytest.raises(SourceCleanupUnconfirmed):
                closing.result(timeout=1)  # never wait the session's default6s
            assert runtime._activations and not runtime._stopped
        finally:
            source.finish.set()
    session.completion.result(timeout=2)
    runtime.stop(timeout=2)
    assert runtime._stopped
