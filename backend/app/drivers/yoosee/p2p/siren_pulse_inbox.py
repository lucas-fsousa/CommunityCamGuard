"""Single pulse admission; claimed cancellations remain owned through OFF cleanup."""

import threading
import time
from collections.abc import Callable
from concurrent.futures import Future
from dataclasses import dataclass, field

from .siren_pulse_job import SirenPulseResult, SirenPulseSpec


def cancelled_result() -> SirenPulseResult:
    return SirenPulseResult('cancelled', False, 0, False, None, None, False, False)


@dataclass(eq=False)
class SirenPulseTicket:
    generation: int
    spec: SirenPulseSpec = field(repr=False)
    admit_until: float
    completion: Future[SirenPulseResult] = field(default_factory=Future, repr=False)
    claimed: bool = False


class SirenPulseInbox:
    def __init__(self, generation: int, *, clock: Callable[[], float] = time.monotonic):
        if type(generation) is not int or not 0 < generation < 1 << 64:
            raise ValueError('siren generation required')
        self._generation, self._clock = generation, clock
        self._lock = threading.Lock()
        self._slot: SirenPulseTicket | None = None
        self._closed = False

    def submit(self, spec: SirenPulseSpec) -> SirenPulseTicket:
        if not isinstance(spec, SirenPulseSpec):
            raise ValueError('typed bounded siren pulse required')
        with self._lock:
            if self._closed or self._slot is not None:
                raise ValueError('siren admission unavailable')
            self._slot = SirenPulseTicket(self._generation, spec, self._clock()+1)
            return self._slot

    def claim(self) -> SirenPulseTicket | None:
        with self._lock:
            ticket = self._slot
            if self._closed or ticket is None or ticket.claimed or ticket.completion.done():
                return None
            if self._clock() >= ticket.admit_until:
                ticket.completion.set_result(cancelled_result())
                return None
            ticket.claimed = True
            return ticket

    def cancel(self, ticket: SirenPulseTicket) -> None:
        with self._lock:
            if self._slot is ticket:
                ticket.completion.cancel()
                if not ticket.claimed or not ticket.completion.cancelled():
                    self._slot = None

    def finish(self, ticket: SirenPulseTicket, result: SirenPulseResult) -> None:
        if not isinstance(result, SirenPulseResult):
            raise ValueError('typed siren completion required')
        with self._lock:
            if self._slot is ticket:
                if result.start_attempted and not result.off_confirmed:
                    self._closed = True
                if not ticket.completion.done():
                    ticket.completion.set_result(result)
                elif ticket.completion.cancelled():
                    self._slot = None

    def take(self, ticket: SirenPulseTicket) -> SirenPulseResult | None:
        with self._lock:
            if self._slot is not ticket or not ticket.completion.done() or ticket.completion.cancelled():
                return None
            self._slot = None
            return ticket.completion.result()

    def close(self) -> None:
        with self._lock:
            self._closed = True
            if self._slot is not None and not self._slot.completion.done():
                if self._slot.claimed:
                    self._slot.completion.cancel()  # Owner must still drain OFF.
                else:
                    self._slot.completion.set_result(cancelled_result())
