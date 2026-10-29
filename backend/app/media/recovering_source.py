"""Sequential source recovery under one registry reservation; no new threads."""

import threading
import time
from collections.abc import Callable

from .mp4_fanout import StreamClosedError, Subscription
from .source_registry import ManagedSource, SourceUnavailable


class RecoveringSource:
    """Only a normal run return certifies cleanup. Exceptions never cause retry.

    The driver supplies failure classification and fresh evidence checks. The
    registry keeps the same camera/domain/owner while this caller-owned run waits.
    No RTSP fallback, authentication renewal or capability inference happens here.
    """

    def __init__(self, initial: ManagedSource, *, factory: Callable[[], ManagedSource],
                 valid: Callable[[], bool], retryable: Callable[[object, ManagedSource], bool],
                 clock: Callable[[], float] = time.monotonic):
        self._current: ManagedSource | None = initial
        self._factory, self._valid, self._retryable = factory, valid, retryable
        self._clock = clock
        self._lock = threading.Lock()
        self._claimed = False
        self._recovering = False
        self._restarts = 0

    @property
    def current(self) -> ManagedSource | None:
        with self._lock:
            return self._current

    @property
    def recovering(self) -> bool:
        with self._lock:
            return self._recovering

    @property
    def restarts(self) -> int:
        with self._lock:
            return self._restarts

    @property
    def recording(self) -> bool:
        return getattr(self.current,"recording",False) is True

    def subscribe(self) -> Subscription:
        source = self.current
        if source is None:
            raise StreamClosedError("source is recovering")
        return source.subscribe()

    def run(self, *, cancelled: Callable[[], bool]) -> object:
        with self._lock:
            if self._claimed:
                raise ValueError("source recovery already started")
            self._claimed = True
        if cancelled() or self._valid() is not True:
            raise SourceUnavailable("source recovery admission unavailable")
        delay = 2.0
        while True:
            source = self.current
            if source is None:
                raise RuntimeError("missing owned source")
            started = self._clock()
            result = source.run(cancelled=cancelled)  # exceptions retain old source; NO retry
            if cancelled() or self._valid() is not True or not self._retryable(result, source):
                return result
            with self._lock:
                self._current, self._recovering = None, True
            if self._clock() - started >= 60:
                delay = 2.0
            until = self._clock() + delay
            check_at = self._clock()
            while self._clock() < until:
                if cancelled():
                    return result
                if self._clock() >= check_at:
                    if self._valid() is not True:
                        return result
                    check_at = self._clock() + 1
                # Caller still owns this run; no worker per retry or unbounded queue.
                time.sleep(min(0.1, max(0.0, until - self._clock())))
            if cancelled() or self._valid() is not True:
                return result
            replacement = self._factory()  # only after confirmed old retirement and backoff
            with self._lock:
                self._current, self._recovering = replacement, False
                self._restarts += 1
            if cancelled() or self._valid() is not True:
                return result
            delay = min(60.0, delay * 2)
