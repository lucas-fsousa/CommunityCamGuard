"""Lazy bounded source workers with explicit, deadline-limited thread retirement."""

import math
import threading
import time
from collections.abc import Callable
from concurrent.futures import Executor, Future
from dataclasses import dataclass
from functools import partial
from queue import Empty, Full, Queue
from typing import Any, TypeVar

from .source_registry import SourceCleanupUnconfirmed, SourceUnavailable

T = TypeVar("T")


@dataclass
class _Job:
    run: Callable[[], object]
    completion: Future


class SourceExecutor(Executor):
    """At most capacity workers + capacity queued jobs. No thread until submit."""

    def __init__(self, *, capacity: int = 1):
        if type(capacity) is not int or not 1 <= capacity <= 32:
            raise ValueError("source worker capacity must be between 1 and 32")
        self._capacity = capacity
        self._lock = threading.Lock()
        self._closed = threading.Event()
        self._queue: Queue[_Job] = Queue(maxsize=capacity)
        self._threads: list[threading.Thread] = []
        self._jobs: set[Future] = set()

    def submit(self, fn: Callable[..., T], /, *args: Any, **kwargs: Any) -> Future[T]:
        with self._lock:
            if self._closed.is_set():
                raise SourceUnavailable("source executor shutting down")
            if self._queue.full():
                raise SourceUnavailable("source executor queue full")
            # Start before publishing this job: a failed Thread.start must not
            # accept work that the caller never received a handle for.
            if len(self._threads) < self._capacity:
                thread = threading.Thread(target=self._work, name="source-owner", daemon=True)
                thread.start()
                self._threads.append(thread)
            completion: Future[T] = Future()
            self._jobs.add(completion)
            try:
                self._queue.put_nowait(_Job(partial(fn, *args, **kwargs), completion))
            except Full:
                self._jobs.discard(completion)
                raise SourceUnavailable("source executor queue full") from None
            return completion

    def _work(self) -> None:
        while True:
            try:
                job = self._queue.get(timeout=0.05)
            except Empty:
                # Submission may have won the race after get() timed out.
                # Check under its lock before declaring the accepted queue drained.
                with self._lock:
                    if self._closed.is_set() and self._queue.empty():
                        return
                continue
            try:
                if job.completion.set_running_or_notify_cancel():
                    try:
                        result = job.run()
                    except BaseException as exc:
                        job.completion.set_exception(exc)
                    else:
                        job.completion.set_result(result)
            finally:
                with self._lock:
                    self._jobs.discard(job.completion)
                self._queue.task_done()
                del job

    def shutdown(self, wait: bool = True, *, cancel_futures: bool = False) -> None:
        with self._lock:
            self._closed.set()
        if cancel_futures:
            while True:
                try:
                    job = self._queue.get_nowait()
                except Empty:
                    break
                job.completion.cancel()  # callbacks must not run under our lock
                with self._lock:
                    self._jobs.discard(job.completion)
                self._queue.task_done()
        if wait:
            self.stop()

    def stop(self, *, timeout: float = 30.0) -> None:
        if isinstance(timeout, bool) or not math.isfinite(timeout) or not 0 <= timeout <= 30:
            raise ValueError("bounded source thread retirement timeout required")
        self.shutdown(wait=False)
        deadline = time.monotonic() + timeout
        with self._lock:
            threads = tuple(self._threads)
        for thread in threads:
            if thread is not threading.current_thread():
                thread.join(timeout=max(0.0, deadline - time.monotonic()))
        with self._lock:
            pending = bool(self._jobs) or not self._queue.empty()
        if pending or any(thread.is_alive() for thread in threads):
            raise SourceCleanupUnconfirmed("source owner thread retirement unconfirmed")
