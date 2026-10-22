"""Bounded decoder stdin writer; no subprocess spawning or camera dependencies."""

import math
import os
import time
from collections.abc import Callable

MAX_PENDING_BYTES = 2 * 1024 * 1024
WRITE_SLICE = 16 * 1024


class NonblockingInput:
    """Own a pipe descriptor and at most one access unit, including partial writes.

    Caller polls pump(); each call attempts one bounded nonblocking write. A stalled
    decoder fails closed instead of dropping half an access unit into its bitstream.
    New decoder epochs require closing this writer and creating a fresh decoder.
    No thread, retry loop, accumulated history or background process is created.
    """

    def __init__(self, fd: int, *, clock: Callable[[], float] = time.monotonic,
                 stall_timeout: float = 1.0):
        self._fd: int | None = fd
        self._pending: memoryview | None = None
        self._clock = clock
        self._last_time: float | None = None
        self._deadline = 0.0
        try:
            if not math.isfinite(stall_timeout) or not 0 < stall_timeout <= 5:
                raise ValueError("invalid decoder input timeout")
            self._timeout = stall_timeout
            self._now()
            os.set_blocking(fd, False)
        except Exception:
            self.close()
            raise

    def _now(self) -> float:
        now = self._clock()
        if not math.isfinite(now) or (self._last_time is not None and now < self._last_time):
            raise ValueError("invalid decoder input clock")
        self._last_time = now
        return now

    @property
    def pending_bytes(self) -> int:
        return len(self._pending) if self._pending is not None else 0

    @property
    def closed(self) -> bool:
        return self._fd is None

    def offer(self, payload: bytes) -> bool:
        """False means busy: retain the frame upstream, never silently discard it."""
        if self.closed:
            raise ValueError("decoder input closed")
        if type(payload) is not bytes or not 0 < len(payload) <= MAX_PENDING_BYTES:
            raise ValueError("invalid decoder access unit")
        if self._pending is not None:
            return False
        try:
            self._deadline = self._now() + self._timeout
            self._pending = memoryview(payload)
            return True
        except Exception:
            self.close()
            raise

    def pump(self) -> int:
        if self._fd is None:
            raise ValueError("decoder input closed")
        try:
            now = self._now()
            if self._pending is None:
                return 0
            if now >= self._deadline:
                raise TimeoutError("decoder input stalled")
            try:
                count = os.write(self._fd, self._pending[:WRITE_SLICE])
            except BlockingIOError:
                return 0
            except InterruptedError:
                return 0
            if count <= 0:
                raise BrokenPipeError("decoder input made no progress")
            self._pending = self._pending[count:] if count < len(self._pending) else None
            # Deadline covers the whole access unit, not each partial write.
            return count
        except Exception:
            self.close()
            raise

    def close(self) -> None:
        fd, self._fd = self._fd, None
        self._pending = None
        if fd is not None:
            os.close(fd)
