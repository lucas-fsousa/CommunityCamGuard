"""Experimental single-channel receive ownership, with no sockets or production wiring.

Create before AV initialization and retain the same object after acceptance. Every
returned message must be consumed immediately, including during initialization.
The caller must poll during silence and close its socket on any ReceiveError.
Acceptance is externally correlated; receiving a packet does not authorize handoff.
"""

from __future__ import annotations

import time
from collections.abc import Callable

from .kcp_receive import KcpReceiver, ReceiveError
from .media_receive import MediaReceiveBatch, MediaReceiver


class ReceiveLifecycle:
    """Bounded experimental lifetime; no reconnect, queue, or sequence reconstruction.

Deadlines deliberately use complete-message progress, not duplicates/ACK traffic.
This is one negotiated inbound conversation, not a multi-channel session manager.
"""

    def __init__(self, peer: tuple[str, int], conv: int, *,
                 initialization_timeout: float = 5.0, idle_timeout: float = 5.0,
                 lifetime: float = 30.0,
                 clock: Callable[[], float] = time.monotonic) -> None:
        for value, upper in ((initialization_timeout, 10), (idle_timeout, 10), (lifetime, 60)):
            if type(value) not in (float, int) or not 0.1 <= value <= upper:
                raise ValueError("invalid receive lifecycle deadline")
        self._clock = clock
        self._started = self._progress = clock()
        self._initialization_timeout = initialization_timeout
        self._idle_timeout = idle_timeout
        self._lifetime = lifetime
        self._media = MediaReceiver(peer, KcpReceiver(conv, clock=clock))
        self._phase = "initializing"

    @property
    def phase(self) -> str:
        return self._phase

    @property
    def buffered_bytes(self) -> int:
        return self._media.receiver.buffered_bytes

    @property
    def next_sequence(self) -> int:
        return self._media.receiver.next_sequence

    def close(self) -> None:
        self._media.receiver.close()
        self._phase = "closed"

    def poll(self) -> None:
        try:
            self._media.receiver.expire()
            now = self._clock()
            if now - self._started >= self._lifetime:
                raise ReceiveError("receive lifetime exceeded")
            if self._phase == "initializing":
                if now - self._started >= self._initialization_timeout:
                    raise ReceiveError("receive initialization deadline exceeded")
            elif now - self._progress >= self._idle_timeout:
                raise ReceiveError("receive progress deadline exceeded")
        except ReceiveError:
            self.close()
            raise

    def activate(self) -> None:
        """Caller has correlated AV acceptance; retain all acknowledged KCP state."""
        self.poll()
        if self._phase != "initializing":
            self.close()
            raise ReceiveError("receive lifecycle already activated")
        self._phase = "active"
        # Handoff itself is not media progress and must not extend the deadlines.

    def receive(self, wire: bytes, peer: tuple[str, int]) -> MediaReceiveBatch:
        self.poll()
        try:
            result = self._media.receive(wire, peer)
        except ReceiveError:
            self.close()
            raise
        if result.messages:
            self._progress = self._clock()
        return result
