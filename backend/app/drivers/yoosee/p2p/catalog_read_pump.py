"""Bridge one resource read to the existing account's sole socket reader."""

from .account_read_dispatcher import AccountReadDispatcher
from .catalog_read_inbox import CatalogReadInbox, CatalogReadTicket
from .catalog_read_job import CatalogReadJob, CatalogReadResult


class CatalogReadPump:
    def __init__(self, dispatcher: AccountReadDispatcher, inbox: CatalogReadInbox):
        self._dispatcher, self._inbox = dispatcher, inbox
        self._job: CatalogReadJob | None = None
        self._ticket: CatalogReadTicket | None = None

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
                    self._job = self._dispatcher.prepare_catalog_read(ticket.spec, deadline=ticket.deadline)
                except ValueError:
                    self._inbox.finish(ticket, CatalogReadResult('unavailable'))
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
