"""Owned native run, evidence heartbeat and fail-closed publication/cleanup."""

import math
import threading
import time
from collections.abc import Callable
from concurrent.futures import Executor, Future

from ..db import source_policy
from .retirement_wait import locked_until, remaining
from .source_offer import NativeSourceOffer
from .source_registry import (
    ManagedSource,
    SourceCleanupUnconfirmed,
    SourceRegistry,
    SourceUnavailable,
)
from .viewer_gate import ViewerGate


class PreparedNative:
    def __init__(self, *, offer: NativeSourceOffer, selection: source_policy.Selection,
                 sources: SourceRegistry, viewers: ViewerGate, clock: Callable[[], float] = time.monotonic):
        self.offer, self.selection = offer, selection
        self._sources, self._viewers = sources, viewers
        self._lock = threading.Lock()
        self._closing = threading.Lock()
        self._closed = False
        self._stop = threading.Event()
        self._clock = clock
        self._next_check = 0.0  # source-run thread confined; never query DB per frame
        self.owner = object()  # registry generation, not a browser/vendor token
        self._constructed = threading.Event()
        self._completion: Future | None = None
        self._published = False
        self.source: ManagedSource | None = None  # Retained actual worker, never construct an observer copy.

    @property
    def was_published(self) -> bool:
        with self._lock:
            return self._published

    @property
    def completion(self) -> Future | None:
        return self._completion

    def request_stop(self) -> None:
        """Nonblocking signal, not a cleanup receipt."""
        self._stop.set()

    def cancelled(self) -> bool:
        if self._stop.is_set():
            return True
        now = self._clock()
        if now >= self._next_check:
            self._next_check = now + 1
            try:
                valid = self.offer.valid() is True
            except Exception:
                valid = False
            if not valid:
                self._stop.set()
        return self._stop.is_set()

    def construct(self):
        if self._stop.is_set() or self.offer.valid() is not True:
            raise SourceUnavailable("native source evidence unavailable")
        self._constructed.set()
        self.source = self.offer.factory()
        return self.source

    def launch(self, executor: Executor) -> Future:
        # Keep submission and handle assignment atomic with close(). A queued or
        # immediately-running job must never be invisible to a cleanup receipt.
        with self._lock:
            if self._stop.is_set() or self._completion is not None:
                raise SourceUnavailable("native source launch unavailable")
            completion = executor.submit(self._sources.run, self.offer.camera_id, domain=self.offer.domain,
                                         factory=self.construct, cancelled=self.cancelled, owner=self.owner)
            self._completion = completion
        # add_done_callback may invoke synchronously: never hold the state lock.
        completion.add_done_callback(self._ended)
        return completion

    def _block_locked(self) -> None:
        self._stop.set()
        try:
            self._viewers.hold(self.offer.camera_id)
        except Exception:
            pass  # retain stop/future even if the external hold budget is exhausted
        if self.selection.phase != "blocked":
            try:
                self.selection = source_policy.block(self.offer.camera_id,
                                                     expected_revision=self.selection.revision)
            except Exception:
                pass  # never overwrite another revision or lose local cancellation

    def _ended(self, _future: Future) -> None:
        # Future callbacks run after SourceRegistry.run has returned/raised.
        # Serialize with publication so a finished source cannot reopen the gate.
        with self._lock:
            if not self._closed:
                self._block_locked()

    def publish(self, apply_routes: Callable[[], None]) -> None:
        with self._lock:
            if (self.completion is None or self.completion.done() or self._stop.is_set()
                    or self.offer.valid() is not True):
                raise SourceUnavailable("native source ended before publication")
            self.selection = source_policy.activate(self.offer.camera_id,
                                                    expected_revision=self.selection.revision)
            apply_routes()  # trusted bounded server publication, never a client command
            if self._stop.is_set() or self.completion.done() or self.offer.valid() is not True:
                raise SourceUnavailable("native source ended during publication")
            self._viewers.resume(self.offer.camera_id)
            self._published = True

    def close(self, *, timeout: float = 15.0) -> None:
        """No fallback/resume. Caller must await this, retaining handle on failure."""
        if isinstance(timeout, bool) or not math.isfinite(timeout) or not 0 < timeout <= 30:
            raise ValueError("bounded native cleanup timeout required")
        self.request_stop()
        deadline = time.monotonic() + timeout
        with locked_until(self._closing, deadline):
            self._close(deadline)

    def _close(self, deadline: float) -> None:
        with locked_until(self._lock, deadline):
            if self._closed:
                return  # an old receipt must not re-hold a later camera generation
            self._block_locked()
        if self.completion is not None:
            try:
                self._sources.stop(self.offer.camera_id, owner=self.owner)
            except SourceCleanupUnconfirmed:
                pass  # still await the actual owner; its reservation remains blocked
            try:
                if not self.completion.cancelled():
                    self.completion.result(timeout=min(6.0, remaining(deadline)))
            except Exception:
                if self._constructed.is_set() or self._sources.owns(self.offer.camera_id, self.owner):
                    raise SourceCleanupUnconfirmed("native owner retirement unconfirmed") from None
        self._viewers.suspend(self.offer.camera_id, timeout=min(8.0, remaining(deadline)))
        with locked_until(self._lock, deadline):
            self._closed = True
