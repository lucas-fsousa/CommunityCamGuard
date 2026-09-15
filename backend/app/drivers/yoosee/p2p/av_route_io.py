"""Per-phase socket budgets for the experimental AV route owner only."""

from __future__ import annotations

import socket
import time
from collections.abc import Callable

from .contracts import P2PProbeError


class BudgetSocket:
    """Small synchronous facade; no queues, threads or independent socket."""

    def __init__(self, sock: socket.socket, cancelled: Callable[[], bool]) -> None:
        self.sock = sock
        self.cancelled = cancelled
        self.phase(20)

    def phase(self, seconds: float, *, cleanup: bool = False) -> None:
        self.deadline = time.monotonic() + seconds
        self.cleanup = cleanup
        self.packets = self.received = self.sent = 0
        self.timeout = 0.1

    def check(self) -> float:
        remaining = self.deadline - time.monotonic()
        if remaining <= 0 or (not self.cleanup and self.cancelled()):
            raise P2PProbeError("native AV route deadline or cancellation")
        return remaining

    def bind(self, address: tuple[str, int]) -> None:
        self.check()
        self.sock.bind(address)

    def getsockname(self) -> tuple[str, int]:
        return self.sock.getsockname()

    def settimeout(self, timeout: float) -> None:
        self.timeout = timeout
        self.sock.settimeout(min(timeout, self.check()))

    def sendto(self, wire: bytes, peer: tuple[str, int]) -> int:
        self.sock.settimeout(min(0.1, self.check()))
        self.sent += len(wire)
        if self.sent > 2 * 1024 * 1024:
            raise P2PProbeError("native AV route transmit budget")
        size = self.sock.sendto(wire, peer)
        if size != len(wire):
            raise P2PProbeError("native AV route partial send")
        return size

    def recvfrom(self, size: int) -> tuple[bytes, tuple[str, int]]:
        self.sock.settimeout(min(self.timeout, self.check()))
        wire, peer = self.sock.recvfrom(min(size, 4096))
        self.check()
        self.packets += 1
        self.received += len(wire)
        if self.packets > 10_000 or self.received > 8 * 1024 * 1024:
            raise P2PProbeError("native AV route receive budget")
        return wire, peer

    def close(self) -> None:
        self.sock.close()
