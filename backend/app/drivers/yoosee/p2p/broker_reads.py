"""Bounded read waiters: transport receipt is not camera response completion.

Single-owner, socket-free preparation for the account dispatcher. No sends,
retries, callbacks, workers, credentials or retained response payloads.
"""

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Literal

from .broker_model_reply import match_model_reply
from .broker_transactions import MAX_PENDING, BrokerTransactions, ReceiptTicket
from .contracts import MODEL_READ_PATHS


@dataclass(frozen=True, slots=True)
class ReadRequest:
    ticket: ReceiptTicket
    device_id: int = field(repr=False)
    path: str


@dataclass(frozen=True, slots=True)
class ReadCompletion:
    request: ReadRequest
    status: Literal["response", "transport_rejected", "timeout", "cancelled"]
    transport_result: int | None
    error_code: int | None = None
    value: object = field(default=None, repr=False)


@dataclass(slots=True)
class _Pending:
    request: ReadRequest
    deadline: float
    transport_result: int | None = None


class BrokerReads:
    """Eight allowlisted B7 reads, <=10 seconds each; explicitly delivered endings.

    Feed only complete decrypted current-peer frames. Generation belongs to the
    authenticated account owner. A response can precede its transport ACK; both
    arrival orders complete exactly once. Caller must poll during socket silence.
    """

    def __init__(self, session_id: int, generation: int, next_sequence: int, *,
                 clock: Callable[[], float] = time.monotonic):
        self._receipts = BrokerTransactions(session_id, generation, next_sequence, clock=clock)
        self._session, self._clock = session_id, clock
        self._pending: dict[int, _Pending] = {}
        self.closed = False

    @property
    def pending_count(self) -> int:
        return len(self._pending)

    def register(self, device_id: int, path: str, timeout: float) -> ReadRequest:
        if self.closed:
            raise ValueError("broker reads closed")
        if (type(device_id) is not int or not 0 < device_id < 1 << 64
                or not isinstance(path, str) or path not in MODEL_READ_PATHS):
            raise ValueError("invalid allowlisted broker read")
        if len(self._pending) >= MAX_PENDING:
            raise ValueError("broker read capacity exhausted")
        now = self._clock()
        ticket = self._receipts.register(0xB7, timeout)
        request = ReadRequest(ticket, device_id, path)
        self._pending[ticket.sequence] = _Pending(request, now + timeout)
        return request

    def _finish(self, pending: _Pending, status: Literal["response", "transport_rejected", "timeout", "cancelled"],
                error_code: int | None = None, value: object = None) -> ReadCompletion:
        ticket = pending.request.ticket
        del self._pending[ticket.sequence]
        self._receipts.cancel(ticket)
        return ReadCompletion(pending.request, status, pending.transport_result, error_code, value)

    def accept(self, plain: bytes, *, generation: int) -> ReadCompletion | None:
        if self.closed:
            return None
        receipt = self._receipts.accept(plain, generation=generation)
        if receipt is not None:
            pending = self._pending.get(receipt.ticket.sequence)
            if pending is not None and self._clock() < pending.deadline:
                pending.transport_result = receipt.result
                if not receipt.accepted:
                    return self._finish(pending, "transport_rejected")
            return None  # Positive ACK leaves application waiter and deadline intact.
        for pending in self._pending.values():
            if self._clock() >= pending.deadline:
                continue
            request = pending.request
            response = match_model_reply(plain, request.ticket, generation=generation,
                                         session_id=self._session, device_id=request.device_id)
            if response is not None:
                return self._finish(pending, "response", response.error_code, response.value)
        return None

    def poll(self) -> tuple[ReadCompletion, ...]:
        now = self._clock()
        expired = [pending for pending in self._pending.values() if now >= pending.deadline]
        results = tuple(self._finish(pending, "timeout") for pending in expired)
        self._receipts.poll()
        return results

    def cancel(self, ticket: ReceiptTicket) -> ReadCompletion | None:
        pending = self._pending.get(ticket.sequence)
        if pending is None or pending.request.ticket != ticket:
            return None
        return self._finish(pending, "cancelled")

    def close(self) -> tuple[ReadCompletion, ...]:
        results = tuple(self._finish(pending, "cancelled") for pending in list(self._pending.values()))
        self._receipts.close()
        self._session = 0
        self.closed = True
        return results
