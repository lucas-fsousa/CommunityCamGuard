"""Single-owner, socket-free adapter for concurrent bounded read callers."""

import time
from collections.abc import Callable
from dataclasses import dataclass, field

from .broker_account import AccountEvents, AccountRead, BrokerAccount, Retirement
from .broker_mailbox import BrokerMailbox, MailboxTicket
from .broker_reads import ReadRequest
from .broker_transactions import ReceiptTicket
from .contracts import CertifiedNode


@dataclass(frozen=True, slots=True)
class DispatchRead:
    ticket: MailboxTicket
    prepared: AccountRead = field(repr=False)


@dataclass(frozen=True, slots=True)
class DispatchBatch:
    reads: tuple[DispatchRead, ...] = ()
    maintenance: AccountEvents = field(default_factory=AccountEvents, repr=False)


class AccountReadDispatcher:
    """One owner drives step/receive/close; callers only access the mailbox.

    The future socket worker must check mailbox.ready immediately before each
    write, send maintenance frames, and call step during silence. No I/O, retry,
    authentication or thread creation here. Supplied clock is shared by all layers.
    """

    def __init__(self, node: CertifiedNode, generation: int, local_ip: str, local_port: int, *,
                 clock: Callable[[], float] = time.monotonic):
        self.mailbox = BrokerMailbox(generation, clock=clock)
        self._account = BrokerAccount(node, generation, local_ip, local_port, clock=clock)
        self._clock = clock
        self._requests: dict[MailboxTicket, ReadRequest] = {}
        self._routes: dict[ReceiptTicket, MailboxTicket] = {}

    def _release(self, ticket: MailboxTicket) -> None:
        request = self._requests.pop(ticket, None)
        if request is not None:
            self._routes.pop(request.ticket, None)
            self._account.cancel_read(request.ticket)
        self.mailbox.acknowledge_cancel(ticket)

    def _publish(self, events: AccountEvents) -> AccountEvents:
        for completion in events.reads:
            ticket = self._routes.pop(completion.request.ticket, None)
            if ticket is not None:
                self._requests.pop(ticket, None)
                self.mailbox.finish(ticket, completion)
        if events.retired is not None:
            self.mailbox.close()
            for ticket in self.mailbox.poll().cancellations:
                self._release(ticket)
        # Caller results go exclusively to the bounded mailbox, not a second queue.
        return AccountEvents(heartbeats=events.heartbeats, outgoing=events.outgoing,
                             retired=events.retired)

    def step(self) -> DispatchBatch:
        maintenance = self._publish(self._account.tick())
        work = self.mailbox.poll()
        for ticket in work.cancellations:
            self._release(ticket)
        prepared = []
        for submission in work.submissions:
            remaining = submission.deadline - self._clock()
            if remaining <= 0:
                continue  # Next poll delivers timeout; do not grant a fresh deadline.
            try:
                read = self._account.submit_read(submission.device_id, submission.path, remaining)
            except Exception:
                return DispatchBatch(maintenance=self.close("prepare"))
            if not self.mailbox.bind(submission.ticket, read.request):
                self._account.cancel_read(read.request.ticket)
                continue
            self._requests[submission.ticket] = read.request
            self._routes[read.request.ticket] = submission.ticket
            prepared.append(DispatchRead(submission.ticket, read))
        return DispatchBatch(tuple(prepared), maintenance)

    def receive(self, plain: bytes, peer: tuple[str, int], *, generation: int) -> AccountEvents:
        return self._publish(self._account.receive(plain, peer, generation=generation))

    def close(self, reason: Retirement = "closed") -> AccountEvents:
        return self._publish(self._account.retire(reason))

    def drain(self) -> AccountEvents:
        self.mailbox.drain()
        return self._publish(self._account.drain())
