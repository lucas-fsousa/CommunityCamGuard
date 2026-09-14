"""Bounded receive-only KCP message assembly for experimental native media.

No socket, camera command, background task or production transport activation.
The caller must verify the peer/MTP frame, pin the negotiated conversation, and
poll expire() during silence. A fatal error requires closing the session: never
discard acknowledged fragments and continue on the same reliable channel.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass

from .media_protocol import KCP_HEADER, KCP_PUSH, MTP_MAX_FRAME_SIZE, MTP_PREFIX_SIZE, KcpSegment

_MASK = 0xFFFFFFFF


class ReceiveError(ValueError):
    """Sanitized terminal receive failure; caller must close the session."""


@dataclass(frozen=True, slots=True)
class ReceiveResult:
    acknowledge: bool
    unacknowledged: int
    messages: tuple[bytes, ...] = ()


class KcpReceiver:
    """One conversation; bounded payload plus at most 128 segment objects.

    Sequence zero is the default only for a newly negotiated channel, never a
    guess based on the first packet observed. Message mode requires a descending
    fragment counter ending at zero. Stream mode/codec framing is not inferred.
    """

    def __init__(self, conv: int, *, initial_sequence: int = 0,
                 max_segments: int = 128, max_bytes: int = 256 * 1024,
                 timeout: float = 2.0, clock: Callable[[], float] = time.monotonic) -> None:
        if any(type(n) is not int or not 0 <= n <= _MASK for n in (conv, initial_sequence)):
            raise ValueError("invalid KCP conversation/initial sequence")
        if type(max_segments) is not int or not 1 <= max_segments <= 128:
            raise ValueError("invalid KCP segment budget")
        if type(max_bytes) is not int or not 1 <= max_bytes <= 1024 * 1024:
            raise ValueError("invalid KCP byte budget")
        if type(timeout) not in (int, float) or not 0.1 <= timeout <= 10:
            raise ValueError("invalid KCP assembly timeout")
        self.conv, self.next_sequence = conv, initial_sequence
        self.max_segments, self.max_bytes = max_segments, max_bytes
        self.timeout, self.clock = timeout, clock
        self._pending: dict[int, KcpSegment] = {}
        self._parts: list[bytes] = []
        self._remaining: int | None = None
        self.buffered_bytes = 0
        self._since: float | None = None
        self.closed = False

    def close(self) -> None:
        self.closed = True
        self._pending.clear()
        self._parts.clear()
        self._remaining = None
        self.buffered_bytes = 0
        self._since = None

    @property
    def available_window(self) -> int:
        return 0 if self.closed else self.max_segments - len(self._pending) - len(self._parts)

    def _fail(self, reason: str) -> None:
        self.close()
        raise ReceiveError(reason)

    def expire(self) -> None:
        if self.closed:
            raise ReceiveError("KCP receiver is closed")
        if self._since is not None and self.clock() - self._since >= self.timeout:
            self._fail("KCP assembly deadline exceeded")

    def receive(self, segment: KcpSegment) -> ReceiveResult:
        self.expire()
        if segment.conv != self.conv or segment.command != KCP_PUSH:
            return ReceiveResult(False, self.next_sequence)
        if (type(segment.sequence) is not int or not 0 <= segment.sequence <= _MASK
                or type(segment.fragment) is not int or not 0 <= segment.fragment < self.max_segments
                or not isinstance(segment.body, bytes)
                or len(segment.body) > MTP_MAX_FRAME_SIZE - MTP_PREFIX_SIZE - KCP_HEADER.size):
            self._fail("invalid KCP receive segment")
        distance = (segment.sequence - self.next_sequence) & _MASK
        if distance >= 0x80000000:
            # Already consumed: acknowledge again, but never emit the message twice.
            return ReceiveResult(True, self.next_sequence)
        if distance >= self.max_segments:
            return ReceiveResult(False, self.next_sequence)
        previous = self._pending.get(segment.sequence)
        if previous is not None:
            if previous.body != segment.body or previous.fragment != segment.fragment:
                self._fail("conflicting KCP duplicate")
            return ReceiveResult(True, self.next_sequence)
        if (len(self._pending) + len(self._parts) >= self.max_segments
                or self.buffered_bytes + len(segment.body) > self.max_bytes):
            self._fail("KCP receive budget exceeded")
        if self._since is None:
            self._since = self.clock()
        self._pending[segment.sequence] = segment
        self.buffered_bytes += len(segment.body)
        messages: list[bytes] = []
        while self.next_sequence in self._pending:
            current = self._pending.pop(self.next_sequence)
            if self._remaining is not None and current.fragment != self._remaining:
                self._fail("invalid KCP fragment chain")
            self._parts.append(current.body)
            self._remaining = current.fragment - 1
            self.next_sequence = (self.next_sequence + 1) & _MASK
            if current.fragment == 0:
                message = b"".join(self._parts)
                messages.append(message)
                self.buffered_bytes -= len(message)
                self._parts.clear()
                self._remaining = None
        if not self._pending and not self._parts:
            self._since = None
        return ReceiveResult(True, self.next_sequence, tuple(messages))
