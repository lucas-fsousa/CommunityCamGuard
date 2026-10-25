"""Sole-reader bridge between one lamp-read inbox and the shared broker namespace."""

import time
from collections.abc import Callable

from .account_read_dispatcher import AccountReadDispatcher
from .lamp_read_inbox import LampReadInbox, LampReadTicket
from .lamp_read_job import LampReadJob, LampReadResult


class LampReadPump:
    def __init__(self, dispatcher: AccountReadDispatcher, inbox: LampReadInbox, *,
                 clock: Callable[[], float] = time.monotonic):
        self._dispatcher, self._inbox, self._clock = dispatcher, inbox, clock
        self._job: LampReadJob | None = None
        self._ticket: LampReadTicket | None = None

    def poll(self) -> tuple[bytes, ...]:
        if self._job is not None and self._ticket is not None:
            if self._inbox.should_cancel(self._ticket):
                self._job.cancel()
            if self._job.result is not None:
                self._inbox.finish(self._ticket, self._job.result)
                self._job = self._ticket = None
        if self._job is None:
            claimed = self._inbox.claim()
            if claimed is not None:
                ticket, spec, deadline = claimed
                remaining = deadline - self._clock()
                if remaining <= 0:
                    self._inbox.finish(ticket, LampReadResult("timeout"))
                    return ()
                try:
                    self._job = self._dispatcher.prepare_lamp_read(spec, deadline=deadline)
                except ValueError:
                    self._inbox.finish(ticket, LampReadResult("unavailable"))
                    return ()
                self._ticket = ticket
                if self._inbox.should_cancel(ticket):
                    self._job.cancel()
        if self._job is not None:
            packet = self._job.next_packet()
            if packet is not None:
                return (packet,)
        return ()

    def observe(self, plain: bytes, peer: tuple[str, int], *, generation: int) -> tuple[bytes, ...]:
        return self._job.observe(plain, peer, generation=generation) if self._job is not None else ()

    def close(self) -> None:
        if self._job is not None:
            self._job.cancel()
        self._job = self._ticket = None
        self._inbox.close()
