"""One socket-free heartbeat waiter using the account's sequence namespace."""

import math
import time
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from typing import Literal

from .access_protocol import build_heartbeat
from .broker_heartbeat_reply import match_heartbeat_reply
from .broker_sequences import BrokerSequences
from .broker_transactions import MAX_TIMEOUT, ReceiptTicket
from .contracts import CertifiedNode


@dataclass(frozen=True, slots=True)
class PreparedHeartbeat:
    ticket: ReceiptTicket
    wire: bytes = field(repr=False)


@dataclass(frozen=True, slots=True)
class HeartbeatCompletion:
    ticket: ReceiptTicket
    status: Literal["response", "timeout", "cancelled"]
    interval_hint: int | None = None


class BrokerHeartbeat:
    """Single-owner, one pending request, <=10 seconds, no retransmission/timer.

    A0 transport ACKs never complete an A1 waiter. Timeout requires the account
    owner to decide retirement; this object neither renews nor retries sessions.
    """

    def __init__(self, session_id: int, generation: int, sequences: BrokerSequences, *,
                 clock: Callable[[], float] = time.monotonic):
        if not sequences.matches(session_id, generation):
            raise ValueError("heartbeat sequence identity mismatch")
        self._session, self._generation, self._sequences = session_id, generation, sequences
        self._clock = clock
        self._pending: tuple[ReceiptTicket, float] | None = None
        self._interval_hint = 50
        self.closed = False

    @property
    def cadence_seconds(self) -> int:
        """SDK wrapper selection, not arbitrary interval or timer phase emulation."""
        return self._interval_hint if self._interval_hint in (35, 40) else 50

    def prepare(self, node: CertifiedNode, local_ip: str, local_port: int,
                timeout: float = 2.0) -> PreparedHeartbeat:
        if self.closed or not self._sequences.matches(self._session, self._generation):
            raise ValueError("heartbeat owner closed")
        if node.session_id != self._session:
            raise ValueError("heartbeat node identity mismatch")
        if self._pending is not None:
            raise ValueError("heartbeat already pending; poll or cancel explicitly")
        if (type(timeout) not in (int, float) or not math.isfinite(timeout)
                or not 0 < timeout <= MAX_TIMEOUT):
            raise ValueError("invalid heartbeat deadline")
        ticket = ReceiptTicket(self._generation, self._sequences.allocate(), 0xA0)
        deadline = self._clock() + timeout
        wire = build_heartbeat(replace(node, next_sequence=ticket.sequence), local_ip, local_port)
        self._pending = ticket, deadline
        return PreparedHeartbeat(ticket, wire)

    def accept(self, plain: bytes, *, generation: int) -> HeartbeatCompletion | None:
        if (self.closed or self._pending is None
                or not self._sequences.matches(self._session, self._generation)):
            return None
        ticket, deadline = self._pending
        if self._clock() >= deadline:
            return None
        reply = match_heartbeat_reply(plain, ticket, generation=generation, session_id=self._session)
        if reply is None:
            return None
        self._pending = None
        if reply.interval_hint is not None:
            self._interval_hint = reply.interval_hint
        return HeartbeatCompletion(ticket, "response", reply.interval_hint)

    def poll(self) -> HeartbeatCompletion | None:
        if self._pending is None or self._clock() < self._pending[1]:
            return None
        ticket, _ = self._pending
        self._pending = None
        return HeartbeatCompletion(ticket, "timeout")

    def cancel(self, ticket: ReceiptTicket) -> HeartbeatCompletion | None:
        if self._pending is None or self._pending[0] != ticket:
            return None
        self._pending = None
        return HeartbeatCompletion(ticket, "cancelled")

    def close(self) -> HeartbeatCompletion | None:
        self.closed = True
        return self.cancel(self._pending[0]) if self._pending else None
