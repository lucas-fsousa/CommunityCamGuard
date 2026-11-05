"""Typed pulse work on the sole account reader; cancellation never abandons OFF."""

from .account_output_budget import AccountOutputBudget
from .account_read_dispatcher import AccountReadDispatcher
from .siren_pulse_inbox import SirenPulseInbox, SirenPulseTicket, cancelled_result
from .siren_pulse_job import SirenPacket, SirenPulseJob, SirenPulseResult


class SirenPulsePump:
    def __init__(self, dispatcher: AccountReadDispatcher, inbox: SirenPulseInbox, output: AccountOutputBudget):
        self._dispatcher, self._inbox, self._output = dispatcher, inbox, output
        self._job: SirenPulseJob | None = None
        self._ticket: SirenPulseTicket | None = None
        self.last_result: SirenPulseResult | None = None

    @property
    def active(self) -> bool:
        return self._job is not None and self._job.result is None

    @property
    def unsafe(self) -> bool:
        result = self._job.result if self._job is not None else self.last_result
        return result is not None and result.start_attempted and not result.off_confirmed

    def poll(self) -> tuple[SirenPacket, ...]:
        if self._job is not None and self._ticket is not None:
            if self._ticket.completion.cancelled():
                self._job.cancel()
            if self._job.result is not None:
                self.last_result = self._job.result
                self._inbox.finish(self._ticket, self.last_result)
                self._job = self._ticket = None
        if self._job is None and not self.unsafe:
            ticket = self._inbox.claim()
            if ticket is not None:
                try:
                    self._job = self._dispatcher.prepare_siren_pulse(ticket.spec, self._output,
                                                                   admit_until=ticket.admit_until)
                except ValueError:
                    self._inbox.finish(ticket, cancelled_result())
                    return ()
                self._ticket = ticket
                if ticket.completion.cancelled():
                    self._job.cancel()
        if self._job is not None:
            packet = self._job.next_packet()
            if packet is not None:
                return (packet,)
        return ()

    def observe(self, plain: bytes, peer: tuple[str, int], *, generation: int) -> tuple[SirenPacket, ...]:
        return self._job.observe(plain, peer, generation=generation) if self._job is not None else ()

    def send_failed(self) -> None:
        if self._job is not None:
            self._job.send_failed()

    def cancel(self) -> None:
        self._inbox.close()
        if self._job is not None:
            self._job.cancel()
