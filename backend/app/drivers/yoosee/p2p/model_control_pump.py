"""Typed writes are driven only by the account's existing sole socket reader."""

import time
from collections.abc import Callable

from .account_read_dispatcher import AccountReadDispatcher
from .model_control_inbox import ControlWriteInbox, ControlWriteTicket
from .model_control_job import ControlWriteJob, ControlWriteResult


class ControlWritePump:
    def __init__(self, dispatcher: AccountReadDispatcher, inbox: ControlWriteInbox, *,
                 clock: Callable[[], float] = time.monotonic):
        self._dispatcher, self._inbox, self._clock = dispatcher, inbox, clock
        self._job: ControlWriteJob | None = None
        self._ticket: ControlWriteTicket | None = None

    def poll(self) -> tuple[bytes, ...]:
        if self._job is not None and self._ticket is not None:
            if self._ticket.completion.cancelled():
                self._job.cancel()
            if self._job.result is not None:
                self._inbox.finish(self._ticket, self._job.result)
                self._job = self._ticket = None
        if self._job is None:
            ticket = self._inbox.claim()
            if ticket is not None:
                try:
                    self._job = self._dispatcher.prepare_control_write(ticket.spec, deadline=ticket.deadline)
                except ValueError:
                    self._inbox.finish(ticket, ControlWriteResult('unavailable'))
                    return ()
                self._ticket = ticket
                if ticket.completion.cancelled():
                    self._job.cancel()
        if self._job is not None:
            packet = self._job.next_packet()
            if packet is not None:
                return (packet,)
        return ()

    def observe(self, plain: bytes, peer: tuple[str, int], *, generation: int) -> None:
        if self._job is not None:
            self._job.observe(plain, peer, generation=generation)

    def close(self) -> None:
        if self._job is not None:
            self._job.cancel()
        self._job = self._ticket = None
        self._inbox.close()
