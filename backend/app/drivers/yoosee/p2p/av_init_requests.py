"""SDK application-level INIT retries, distinct from KCP retransmission.

Pinned SDK iv_on_timeout_send_reqconn queues a fresh INIT until ACCEPT.
Saved app traffic confirms fresh KCP sequences at 0/50/120/220ms. This owner
is opt-in for experimental UDP only; TCP's existing handshake is unchanged.
"""

import time
from collections.abc import Callable

from .av_control_send import ReliableAvControl
from .kcp_receive import ReceiveError


class AvInitRequests:
    def __init__(self, peer: tuple[str, int], link_id: int, call_id: int, *,
                 request_user_data: bytes | None = None,
                 clock: Callable[[], float] = time.monotonic):
        self._peer, self._link, self._call = peer, link_id, call_id
        self._data, self._clock = request_user_data, clock
        self._requests: list[ReliableAvControl] = []
        self._next = clock()
        self._count = 0
        self._stopped = False
        self.attempts = 0
        self.acknowledged = False
        self.closed = False

    def _clear(self) -> None:
        for request in self._requests:
            request.close()
        self._requests.clear()

    def close(self) -> None:
        self.closed = True
        self._data = None
        self._clear()

    def stop_repeating(self) -> None:
        self._stopped = True
        if self.acknowledged:
            self._clear()

    def poll(self) -> None:
        if self.closed:
            raise ReceiveError("AV INIT requests are closed")
        for request in self._requests:
            request.poll()

    def due(self) -> bytes | None:
        self.poll()
        # Each KCP request retains its own identical-byte retry/receipt contract.
        for request in self._requests:
            wire = request.due()
            if wire is not None:
                self.attempts += 1
                return wire
        now = self._clock()
        if self._stopped or self._count >= 25 or now < self._next:
            return None
        request = ReliableAvControl(self._peer, self._link, self._call, action=1,
            sequence=self._count, request_user_data=self._data, clock=self._clock,
            _sdk_init_repeat=True)
        self._requests.append(request)
        self._next = now + ((0.05, 0.07, 0.1)[self._count] if self._count < 3 else 0.35)
        self._count += 1
        self.attempts += 1
        return request.due()

    def receive_ack(self, wire: bytes, peer: tuple[str, int]) -> bool:
        self.poll()
        received = False
        for request in self._requests:
            received = request.receive_ack(wire, peer) or received
        self.acknowledged |= received
        if self.acknowledged and self._stopped:
            self._clear()
        return received
