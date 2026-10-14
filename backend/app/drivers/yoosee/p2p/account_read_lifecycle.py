"""Experimental generation handoff around the finite identity-read worker."""

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Literal

from .account_read_worker import AccountReadWorker, WorkerSummary
from .broker_mailbox import BrokerMailbox, MailboxTicket, ReadOutcome
from .broker_read_socket import ReadSocket, ReadSpec
from .contracts import CertifiedNode


@dataclass(frozen=True, slots=True)
class ReadConnection:
    node: CertifiedNode = field(repr=False)
    socket: ReadSocket = field(repr=False)
    local_ip: str = field(repr=False)
    local_port: int = field(repr=False)


@dataclass(frozen=True, slots=True)
class ReadBatchSummary:
    worker: WorkerSummary
    outcomes: tuple[ReadOutcome | None, ...] = field(repr=False)


class AccountReadLifecycle:
    """One trusted account, sequential generations, no thread or automatic login.

    The caller retains external account/enrollment reservations across run().
    connect must be bounded and transfer a NEW exclusively owned socket, never
    a cached PTZ socket. Until it returns, it owns all cleanup on failure. Failed
    connect/run leaves this coordinator blocked: no implicit authentication retry.
    This does not yet coordinate legacy production login paths.
    """

    def __init__(self, *, clock: Callable[[], float] = time.monotonic):
        self._clock = clock
        self._lock = threading.Lock()
        self._state: Literal["idle", "connecting", "active", "draining", "blocked"] = "idle"
        self._generation = 0
        self._worker: AccountReadWorker | None = None
        self._drain = False
        self._stop = False

    @property
    def state(self) -> str:
        with self._lock:
            return self._state

    def submit(self, device_id: int, path: str, timeout: float = 2.0) -> MailboxTicket:
        with self._lock:
            if self._state != "active" or self._worker is None:
                raise ValueError("account not accepting reads")
            return self._worker.mailbox.submit(device_id, path, timeout)

    def take(self, ticket: MailboxTicket) -> ReadOutcome | None:
        with self._lock:
            if ticket.generation != self._generation or self._worker is None:
                return None
            return self._worker.mailbox.take(ticket)

    def cancel(self, ticket: MailboxTicket) -> bool:
        with self._lock:
            if ticket.generation != self._generation or self._worker is None:
                return False
            return self._worker.mailbox.cancel(ticket)

    def drain(self) -> None:
        """Immediately reject admission, then let the sole reader drain <=2s."""
        with self._lock:
            if self._state not in ("connecting", "active", "draining"):
                return
            self._state, self._drain = "draining", True
            if self._worker is not None:
                self._worker.mailbox.drain()
                self._worker.drain()

    def stop(self) -> None:
        with self._lock:
            if self._state not in ("connecting", "active", "draining"):
                return
            self._state, self._stop = "draining", True
            if self._worker is not None:
                self._worker.mailbox.close()
                self._worker.stop()

    def run(self, connect: Callable[[int], ReadConnection]) -> WorkerSummary:
        return self._run(connect, (), lambda: False).worker

    def run_reads(self, connect: Callable[[int], ReadConnection], reads: tuple[ReadSpec, ...], *,
                  cancelled: Callable[[], bool] = lambda: False) -> ReadBatchSummary:
        """Trusted bounded bootstrap batch; retain results before admitting replacement."""
        if not isinstance(reads, tuple) or not 1 <= len(reads) <= 8:
            raise ValueError("bounded initial read tuple required")
        # Reuse admission validation before claiming/authenticating. No wire I/O.
        validation = BrokerMailbox(1)
        for spec in reads:
            if not isinstance(spec, ReadSpec):
                raise ValueError("invalid initial read specification")
            validation.submit(spec.device_id, spec.path, spec.timeout)
        validation.close()
        del validation
        return self._run(connect, reads, cancelled)

    def _run(self, connect: Callable[[int], ReadConnection], reads: tuple[ReadSpec, ...],
             cancelled: Callable[[], bool]) -> ReadBatchSummary:
        """Claim before auth; permit replacement only after verified physical close.

        No I/O under the lifecycle lock. Old results are retained only until the
        next explicit run; old tickets never address the next generation. Nothing
        is resubmitted, and no historical result queue accumulates.
        """
        with self._lock:
            if self._state != "idle":
                raise ValueError("account busy or cleanup unconfirmed")
            if self._generation >= (1 << 64) - 1:
                self._state = "blocked"
                raise ValueError("account generation exhausted")
            self._generation += 1
            generation = self._generation
            self._state, self._worker = "connecting", None
            self._drain = self._stop = False

        connection = None
        worker = None
        transferred = False
        summary = None
        try:
            connection = connect(generation)
            worker = AccountReadWorker(connection.node, generation, connection.local_ip,
                                       connection.local_port, clock=self._clock)
            tickets = tuple(worker.mailbox.submit(r.device_id, r.path, r.timeout) for r in reads)
            with self._lock:
                self._worker = worker
                if self._stop:
                    worker.mailbox.close()
                    worker.stop()
                elif self._drain:
                    worker.mailbox.drain()
                    worker.drain()
                else:
                    self._state = "active"
            transferred = True
            summary = worker.run(connection.socket, cancelled=cancelled)
            return ReadBatchSummary(summary, tuple(worker.mailbox.take(t) for t in tickets))
        finally:
            try:
                if connection is not None and not transferred:
                    if worker is not None:
                        worker.stop()
                        worker.run(connection.socket)
                    else:
                        connection.socket.close()
            finally:
                with self._lock:
                    # An exception has no verified WorkerSummary. Fail closed,
                    # including interruptions even if the worker attempted close.
                    self._state = ("idle" if summary is not None
                                   and not summary.socket_close_failed else "blocked")
