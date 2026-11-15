"""One bounded KCP PUSH, driven by the existing AV reader; no socket I/O."""

import time
from collections.abc import Callable

from .kcp_receive import DEFAULT_RECEIVE_SEGMENTS, ReceiveError
from .media_protocol import KCP_ACK, MTP_MAX_FRAME_SIZE, build_kcp_push, parse_kcp_segments


class ReliableKcpPush:
    """Four identical transmissions, two-second deadline, exact transport receipt.

    The caller owns sequence allocation and supplies every incoming datagram.
    Receipt does NOT mean that a command executed or audio reached the speaker.
    """

    _label = "KCP"

    def __init__(self, peer: tuple[str, int], conv: int, sequence: int, body: bytes, *,
                 clock: Callable[[], float] = time.monotonic):
        if any(type(n) is not int or not 0 <= n <= 0xFFFFFFFF for n in (conv, sequence)):
            raise ValueError("invalid KCP outgoing sequence or conversation")
        if not isinstance(body, bytes) or not body or len(body) > 1024:
            raise ValueError("invalid bounded KCP outgoing body")
        self._sequence, self._conv, self._peer, self._clock = sequence, conv, peer, clock
        self._created = clock()
        self._timestamp = int(self._created * 1000) & 0xFFFFFFFF
        self._wire = build_kcp_push(conv, sequence, body, timestamp=self._timestamp,
                                    window=DEFAULT_RECEIVE_SEGMENTS)
        self._last_sent: float | None = None
        self.attempts = 0
        self.acknowledged = False
        self.peer_next_sequence: int | None = None
        self.closed = False
        self._suspended = False

    @property
    def sequence(self) -> int:
        return self._sequence

    def suspend_receipt(self) -> None:
        """Retain an emitted INIT for late receipts without expiring live video."""
        self._suspended = True

    def resume_receipt(self) -> None:
        """A bounded owner-requested drain; same wire, sequence and timestamp."""
        if self._suspended and not self.acknowledged:
            self._created = self._clock()
            self._last_sent = None
            self.attempts = 0
        self._suspended = False

    def close(self) -> None:
        self._wire = b""
        self.closed = True

    def poll(self) -> None:
        if self.closed:
            raise ReceiveError(f"{self._label} sender is closed")
        if not self._suspended and not self.acknowledged and self._clock() - self._created >= 2:
            self.close()
            raise ReceiveError(f"{self._label} receipt deadline exceeded")

    def due(self) -> bytes | None:
        self.poll()
        now = self._clock()
        if self._suspended or self.acknowledged or self.attempts >= 4:
            return None
        if self._last_sent is not None and now - self._last_sent < 0.25:
            return None
        self._last_sent = now
        self.attempts += 1
        return self._wire

    def receive_ack(self, wire: bytes, peer: tuple[str, int]) -> bool:
        self.poll()
        if self.acknowledged or not self.attempts or peer != self._peer or len(wire) > MTP_MAX_FRAME_SIZE:
            return False
        try:
            segments = parse_kcp_segments(wire)
        except ValueError:
            return False
        for segment in segments:
            if (segment.command == KCP_ACK and segment.conv == self._conv
                    and segment.sequence == self._sequence and segment.timestamp == self._timestamp
                    and segment.fragment == 0 and not segment.body):
                self.acknowledged = True
                self.peer_next_sequence = segment.unacknowledged
                self._wire = b""
                return True
        return False
