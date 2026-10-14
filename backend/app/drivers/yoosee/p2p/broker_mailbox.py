"""Bounded read admission between callers and a future single account worker."""

import math
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Literal

from .broker_reads import ReadCompletion, ReadRequest
from .broker_transactions import MAX_PENDING, MAX_TIMEOUT
from .contracts import ACCOUNT_IDENTITY_READ_PATHS


@dataclass(frozen=True, slots=True)
class MailboxTicket:
    generation: int
    sequence: int


@dataclass(frozen=True, slots=True)
class ReadSubmission:
    ticket: MailboxTicket
    device_id: int = field(repr=False)
    path: str
    deadline: float


@dataclass(frozen=True, slots=True)
class ReadOutcome:
    status: Literal["response", "transport_rejected", "timeout", "cancelled"]
    completion: ReadCompletion | None = field(default=None, repr=False)


@dataclass(frozen=True, slots=True)
class BoundReadOutcome:
    request: ReadRequest | None = field(repr=False)
    outcome: ReadOutcome = field(repr=False)


@dataclass(frozen=True, slots=True)
class MailboxWork:
    submissions: tuple[ReadSubmission, ...]
    cancellations: tuple[MailboxTicket, ...]


@dataclass(slots=True)
class _Slot:
    submission: ReadSubmission
    claimed: bool = False
    outcome: ReadOutcome | None = None
    consumed: bool = False
    cancel_pending: bool = False
    request: ReadRequest | None = None


class BrokerMailbox:
    """Eight total slots, including unconsumed results and unacknowledged cancels.

    No threads/callbacks/sockets created. One worker calls poll/finish; callers
    submit/cancel/take. Lock sections only mutate bounded state, never do I/O.
    Payload bounds are inherited from the account's bounded ReadCompletion decoder.
    """

    def __init__(self, generation: int, *, clock: Callable[[], float] = time.monotonic):
        if type(generation) is not int or generation <= 0:
            raise ValueError("invalid mailbox generation")
        self._generation, self._clock = generation, clock
        self._lock = threading.Lock()
        self._slots: dict[MailboxTicket, _Slot] = {}
        self._next = 0
        self._closed = False

    def submit(self, device_id: int, path: str, timeout: float = 2.0) -> MailboxTicket:
        if (type(device_id) is not int or not 0 < device_id < 1 << 64
                or not isinstance(path, str) or path not in ACCOUNT_IDENTITY_READ_PATHS
                or type(timeout) not in (int, float) or not 0 < timeout <= MAX_TIMEOUT
                or not math.isfinite(timeout)):
            raise ValueError("invalid bounded read submission")
        with self._lock:
            if self._closed or self._next >= 1 << 64:
                raise ValueError("mailbox closed or sequence exhausted")
            if len(self._slots) >= MAX_PENDING:
                raise ValueError("mailbox capacity exhausted")
            ticket = MailboxTicket(self._generation, self._next)
            self._next += 1
            self._slots[ticket] = _Slot(ReadSubmission(ticket, device_id, path, self._clock() + timeout))
            return ticket

    @staticmethod
    def _stop(slot: _Slot, status: Literal["timeout", "cancelled"]) -> None:
        if slot.outcome is None and not slot.consumed:
            slot.outcome = ReadOutcome(status)
            slot.cancel_pending = slot.claimed

    def poll(self) -> MailboxWork:
        """Worker applies cancellations before submissions; no wait or retry."""
        with self._lock:
            now = self._clock()
            submissions, cancellations = [], []
            for ticket, slot in list(self._slots.items()):
                if now >= slot.submission.deadline:
                    self._stop(slot, "timeout")
                if slot.cancel_pending:
                    cancellations.append(ticket)
                if not slot.claimed and slot.outcome is None and not self._closed:
                    slot.claimed = True
                    submissions.append(slot.submission)
            return MailboxWork(tuple(submissions), tuple(cancellations))

    def acknowledge_cancel(self, ticket: MailboxTicket) -> bool:
        """Worker calls only AFTER releasing the associated account reservation."""
        with self._lock:
            slot = self._slots.get(ticket)
            if slot is None or not slot.cancel_pending:
                return False
            slot.cancel_pending = False
            if slot.consumed:
                del self._slots[ticket]
            return True

    def bind(self, ticket: MailboxTicket, request: ReadRequest) -> bool:
        """Bind the unique wire ticket before any send; failure requires cancellation."""
        with self._lock:
            slot = self._slots.get(ticket)
            if (slot is None or not slot.claimed or slot.request is not None
                    or slot.outcome is not None or slot.consumed
                    or self._clock() >= slot.submission.deadline
                    or request.ticket.generation != self._generation
                    or request.ticket.kind != 0xB7
                    or request.device_id != slot.submission.device_id
                    or request.path != slot.submission.path
                    or any(s.request is not None and s.request.ticket == request.ticket
                           for s in self._slots.values())):
                return False
            slot.request = request
            return True

    def finish(self, ticket: MailboxTicket, completion: ReadCompletion) -> bool:
        """Only the worker supplies a terminal result from its bounded decoder."""
        with self._lock:
            slot = self._slots.get(ticket)
            if (slot is None or not slot.claimed or slot.outcome is not None or slot.consumed
                    or slot.request != completion.request):
                return False
            if self._clock() >= slot.submission.deadline:
                slot.outcome = ReadOutcome("timeout")
            else:
                slot.outcome = ReadOutcome(completion.status, completion)
            return True

    def cancel(self, ticket: MailboxTicket) -> bool:
        with self._lock:
            slot = self._slots.get(ticket)
            if slot is None or slot.outcome is not None or slot.consumed:
                return False
            self._stop(slot, "cancelled")
            return True

    def ready(self, ticket: MailboxTicket) -> bool:
        """Worker's final pre-send check; cancellation is best-effort once sent."""
        with self._lock:
            slot = self._slots.get(ticket)
            if slot is None:
                return False
            if self._clock() >= slot.submission.deadline:
                self._stop(slot, "timeout")
            return slot.request is not None and slot.outcome is None and not slot.consumed

    def take(self, ticket: MailboxTicket) -> ReadOutcome | None:
        bound = self.take_bound(ticket)
        return bound.outcome if bound is not None else None

    def take_bound(self, ticket: MailboxTicket) -> BoundReadOutcome | None:
        """Atomically consume result with the ORIGINAL pre-send request binding."""
        with self._lock:
            slot = self._slots.get(ticket)
            if slot is None or slot.consumed:
                return None
            if self._clock() >= slot.submission.deadline:
                self._stop(slot, "timeout")
            outcome = slot.outcome
            if outcome is not None:
                if slot.cancel_pending:
                    slot.outcome, slot.consumed = None, True
                else:
                    del self._slots[ticket]
            return BoundReadOutcome(slot.request, outcome) if outcome is not None else None

    def close(self) -> None:
        """Reject admission; worker still polls cancellations, callers take endings."""
        with self._lock:
            self._closed = True
            for slot in self._slots.values():
                self._stop(slot, "cancelled")

    def drain(self) -> None:
        """Reject new/queued work while allowing already claimed reads to finish."""
        with self._lock:
            self._closed = True
            for slot in self._slots.values():
                if not slot.claimed:
                    self._stop(slot, "cancelled")
