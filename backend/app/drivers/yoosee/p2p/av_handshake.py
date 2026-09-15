"""Socket-free AV INIT/ACCEPT/START ownership for experimental native reception.

No route discovery, actual sends, production registration, PTZ or microphone.
The caller must send returned wires promptly, consume all records and poll silence.
"""

from __future__ import annotations

import time
from collections.abc import Callable

from .av_control_send import ReliableAvControl
from .av_receive import AvReceiveBatch, AvReceiver
from .kcp_receive import ReceiveError


class AvHandshake:
    """One freshly negotiated route; sender and parser objects never get replaced.

Readiness needs INIT receipt, correlated ACCEPT and peer START, local START receipt,
and parsed encoding. Media records can arrive before readiness: consume them with
the same parser/decoder owner, but do not claim ready or present a live source yet.
"""

    def __init__(self, peer: tuple[str, int], link_id: int, call_id: int, cookie: bytes, *,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self._clock, self._created = clock, clock()
        self._peer, self._link_id, self._call_id = peer, link_id, call_id
        self._init = ReliableAvControl(peer, link_id, call_id, action=1, clock=clock)
        self._receiver = AvReceiver(peer, link_id, call_id, cookie,
                                    control_conv=link_id | 0x80000000, clock=clock)
        self._start: ReliableAvControl | None = None
        self._finish: ReliableAvControl | None = None
        self.closed = False

    @property
    def ready(self) -> bool:
        return (not self.closed and self._finish is None and self._init.acknowledged
                and self._receiver.accepted and self._receiver.peer_started
                and self._start is not None and self._start.acknowledged
                and self._receiver.phase == "active")

    @property
    def buffered_bytes(self) -> int:
        return self._receiver.buffered_bytes

    @property
    def close_acknowledged(self) -> bool:
        """Transport receipt only, not proof the peer released its media route."""
        return not self.closed and self._finish is not None and self._finish.acknowledged

    def begin_finish(self) -> None:
        """End a fully negotiated session; no other writer may use this route."""
        self.poll()
        if self._finish is not None:
            return
        if not self.ready:
            raise ReceiveError("AV CLOSE requires completed negotiation")
        # This owner sends exactly one base-channel START at sequence zero.
        # Incoming sequence/UNA and retries do not allocate outgoing sequences.
        self._finish = ReliableAvControl(self._peer, self._link_id, self._call_id,
                                         action=7, sequence=1, clock=self._clock)

    def close(self) -> None:
        self.closed = True
        self._init.close()
        if self._start is not None:
            self._start.close()
        if self._finish is not None:
            self._finish.close()
        self._receiver.close()
        self._call_id = 0

    def poll(self) -> None:
        try:
            if self.closed:
                raise ReceiveError("AV handshake is closed")
            if self._finish is None and not self.ready and self._clock() - self._created >= 5:
                raise ReceiveError("AV handshake readiness deadline exceeded")
            self._init.poll()
            if self._start is not None:
                self._start.poll()
            if self._finish is not None:
                self._finish.poll()
            self._receiver.poll()
        except ReceiveError:
            self.close()
            raise

    def due(self) -> tuple[bytes, ...]:
        """At most one control per call; never replay INIT on a fresh sequence."""
        self.poll()
        try:
            if self._finish is not None:
                wire = self._finish.due()
            elif not self._init.acknowledged:
                wire = self._init.due()
            elif self._receiver.accepted and self._receiver.peer_started:
                if self._start is None:
                    self._start = ReliableAvControl(self._peer, self._link_id, self._call_id,
                                                    action=6, clock=self._clock)
                wire = self._start.due()
            else:
                wire = None
            return (wire,) if wire is not None else ()
        except ReceiveError:
            self.close()
            raise

    def receive(self, wire: bytes, peer: tuple[str, int]) -> AvReceiveBatch:
        self.poll()
        if not self._init.attempts:
            return AvReceiveBatch()  # Unsolicited traffic cannot start negotiation.
        try:
            self._init.receive_ack(wire, peer)
            if self._start is not None:
                self._start.receive_ack(wire, peer)
            if self._finish is not None:
                self._finish.receive_ack(wire, peer)
            return self._receiver.receive(wire, peer)
        except ReceiveError:
            self.close()
            raise
