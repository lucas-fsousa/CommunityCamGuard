"""One generation-bound catalogue read, including unconsumed results; no thread."""

import threading
import time
from collections.abc import Callable
from concurrent.futures import Future
from dataclasses import dataclass, field

from .catalog_read_job import CatalogReadResult, CatalogReadSpec


@dataclass(eq=False)
class CatalogReadTicket:
    generation: int
    spec: CatalogReadSpec = field(repr=False)
    deadline: float
    completion: Future[CatalogReadResult] = field(default_factory=Future, repr=False)
    claimed: bool = False


class CatalogReadInbox:
    def __init__(self, generation: int, *, clock: Callable[[], float] = time.monotonic):
        if type(generation) is not int or not 0 < generation < 1 << 64:
            raise ValueError('catalogue generation required')
        self._generation, self._clock = generation, clock
        self._lock = threading.Lock()
        self._slot: CatalogReadTicket | None = None
        self._closed = False

    def submit(self, spec: CatalogReadSpec) -> CatalogReadTicket:
        if not isinstance(spec, CatalogReadSpec):
            raise ValueError('typed catalogue read required')
        with self._lock:
            if self._closed or self._slot is not None:
                raise ValueError('catalogue read admission unavailable')
            self._slot = CatalogReadTicket(self._generation, spec, self._clock()+2)
            return self._slot

    def claim(self) -> CatalogReadTicket | None:
        with self._lock:
            ticket = self._slot
            if self._closed or ticket is None or ticket.claimed or ticket.completion.done():
                return None
            if self._clock() >= ticket.deadline:
                ticket.completion.set_result(CatalogReadResult('timeout'))
                return None
            ticket.claimed = True
            return ticket

    def cancel(self, ticket: CatalogReadTicket) -> None:
        with self._lock:
            if self._slot is ticket:
                ticket.completion.cancel()
                if not ticket.claimed or (ticket.completion.done() and not ticket.completion.cancelled()):
                    self._slot = None

    def finish(self, ticket: CatalogReadTicket, result: CatalogReadResult) -> None:
        with self._lock:
            if self._slot is ticket:
                if not ticket.completion.done():
                    ticket.completion.set_result(result)
                elif ticket.completion.cancelled():
                    self._slot = None

    def take(self, ticket: CatalogReadTicket) -> CatalogReadResult | None:
        with self._lock:
            if self._slot is not ticket or not ticket.completion.done() or ticket.completion.cancelled():
                return None
            self._slot = None
            return ticket.completion.result()

    def close(self) -> None:
        with self._lock:
            self._closed = True
            if self._slot is not None and not self._slot.completion.done():
                self._slot.completion.set_result(CatalogReadResult('cancelled'))
