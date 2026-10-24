"""Bounded lazy worker ownership; all jobs are local fake callables."""

import threading

import pytest

from backend.app.media.source_executor import SourceExecutor
from backend.app.media.source_registry import SourceCleanupUnconfirmed, SourceUnavailable


def test_idle_executor_has_no_threads_and_closes_without_starting_any():
    executor = SourceExecutor()
    assert not executor._threads
    executor.stop()
    executor.stop()
    assert not executor._threads
    with pytest.raises(SourceUnavailable):
        executor.submit(lambda: None)


def test_queue_is_bounded_and_cancellation_prevents_queued_callable():
    executor = SourceExecutor(capacity=1)
    entered, release, queued_ran = threading.Event(), threading.Event(), threading.Event()
    def running():
        entered.set()
        assert release.wait(2)
        return "done"
    try:
        first = executor.submit(running)
        assert entered.wait(1)
        queued = executor.submit(queued_ran.set)
        with pytest.raises(SourceUnavailable):
            executor.submit(lambda: None)
        executor.shutdown(wait=False, cancel_futures=True)
        assert queued.cancelled() and not queued_ran.is_set()
        with pytest.raises(SourceUnavailable):
            executor.submit(lambda: None)
    finally:
        release.set()
        executor.stop(timeout=2)
    assert first.result() == "done" and not executor._jobs


def test_completed_future_is_not_a_receipt_that_its_callback_thread_has_retired():
    executor = SourceExecutor()
    run, callback, release = threading.Event(), threading.Event(), threading.Event()
    future = executor.submit(lambda: run.wait(2))
    def finishing(_future):
        callback.set()
        assert release.wait(2)
    future.add_done_callback(finishing)
    run.set()
    try:
        assert callback.wait(1) and future.done()
        with pytest.raises(SourceCleanupUnconfirmed):
            executor.stop(timeout=0.01)
        assert executor._threads[0].is_alive()
    finally:
        release.set()
        executor.stop(timeout=2)
    assert not executor._threads[0].is_alive()


def test_failed_thread_start_accepts_no_untracked_job(monkeypatch):
    executor = SourceExecutor()
    monkeypatch.setattr(threading.Thread, "start", lambda _: (_ for _ in ()).throw(OSError("synthetic")))
    with pytest.raises(OSError):
        executor.submit(lambda: pytest.fail("untracked task started"))
    assert executor._queue.empty() and not executor._jobs and not executor._threads
    executor.stop()


@pytest.mark.parametrize("capacity", [0, 33, True])
def test_capacity_has_a_finite_limit(capacity):
    with pytest.raises(ValueError):
        SourceExecutor(capacity=capacity)
