"""Bounded, socket-free reliable transmission of one AV INIT or START control.

Not PTZ, intercom, a general KCP sender or a live session initializer. The owner
must gate START on correlated ACCEPT and combine this with the receive coordinator.
"""

from __future__ import annotations

import time
from collections.abc import Callable

from .kcp_receive import ReceiveError
from .media_protocol import (
    KCP_ACK,
    MTP_MAX_FRAME_SIZE,
    build_av_control,
    build_av_init,
    build_kcp_push,
    parse_kcp_segments,
)


class ReliableAvControl:
    """One sequence-zero request on a freshly negotiated conversation.

At most four transmissions, 250 ms apart, within a two-second absolute deadline.
Retransmit identical bytes/sequence, never manufacture new application requests.
An ACK proves transport receipt only, not acceptance, media readiness or execution.
"""

    def __init__(self, peer: tuple[str, int], link_id: int, call_id: int, *,
                 action: int, clock: Callable[[], float] = time.monotonic) -> None:
        if type(link_id) is not int or not 0 < link_id <= 0xFFFFFF:
            raise ValueError("invalid AV link")
        if type(call_id) is not int or not 0 <= call_id <= 0xFFFFFFFF:
            raise ValueError("invalid AV call")
        if type(action) is not int or action not in (1, 6):
            raise ValueError("only AV INIT/START controls are supported")
        self._peer, self._clock = peer, clock
        self._conv = link_id | 0x80000000 if action == 1 else link_id
        self._created = clock()
        self._timestamp = int(self._created * 1000) & 0xFFFFFFFF
        body = build_av_init(call_id) if action == 1 else build_av_control(call_id, 6)
        self._wire = build_kcp_push(self._conv, 0, body, timestamp=self._timestamp)
        self._last_sent: float | None = None
        self.attempts = 0
        self.acknowledged = False
        self.closed = False

    def close(self) -> None:
        self._wire = b""
        self.closed = True

    def poll(self) -> None:
        if self.closed:
            raise ReceiveError("AV control sender is closed")
        if not self.acknowledged and self._clock() - self._created >= 2:
            self.close()
            raise ReceiveError("AV control receipt deadline exceeded")

    def due(self) -> bytes | None:
        """Return one attempted transmission; caller sends it to the pinned peer.

        Call close() on send failure/session cancellation. A returned wire counts
        as attempted even if the caller fails to send; no hidden transport retry.
        """
        self.poll()
        now = self._clock()
        if self.acknowledged or self.attempts >= 4:
            return None
        if self._last_sent is not None and now - self._last_sent < 0.25:
            return None
        self._last_sent = now
        self.attempts += 1
        return self._wire

    def receive_ack(self, wire: bytes, peer: tuple[str, int]) -> bool:
        self.poll()
        if self.acknowledged or self.attempts == 0 or peer != self._peer or len(wire) > MTP_MAX_FRAME_SIZE:
            return False
        try:
            segments = parse_kcp_segments(wire)
        except ValueError:
            return False
        for segment in segments:
            if (segment.command == KCP_ACK and segment.conv == self._conv
                    and segment.sequence == 0 and segment.timestamp == self._timestamp
                    and segment.fragment == 0 and not segment.body):
                self.acknowledged = True
                self._wire = b""
                return True
        return False
