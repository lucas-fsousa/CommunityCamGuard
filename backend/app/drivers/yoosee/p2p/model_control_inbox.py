"""One generation-bound typed control submission; cancellation never repeats a write."""

import threading
import time
from collections.abc import Callable
from concurrent.futures import Future
from dataclasses import dataclass, field

from .model_control_job import (
    ControlWriteResult,
    ControlWriteSpec,
    OrientationWriteSpec,
    VolumeWriteSpec,
)


@dataclass(eq=False)
class ControlWriteTicket:
    generation: int
    spec: ControlWriteSpec = field(repr=False)
    deadline: float
    completion: Future[ControlWriteResult] = field(default_factory=Future, repr=False)
    claimed: bool = False


class ControlWriteInbox:
    def __init__(self, generation: int, *, clock: Callable[[], float] = time.monotonic):
        if type(generation) is not int or not 0 < generation < 1 << 64:
            raise ValueError('volume generation required')
        self._generation, self._clock = generation, clock
        self._lock = threading.Lock()
        self._slot: ControlWriteTicket | None = None
        self._closed = False

    def submit(self, spec: ControlWriteSpec) -> ControlWriteTicket:
        if not isinstance(spec, (VolumeWriteSpec, OrientationWriteSpec)):
            raise ValueError('typed control required')
        with self._lock:
            if self._closed or self._slot is not None:
                raise ValueError('volume write admission unavailable')
            self._slot = ControlWriteTicket(self._generation, spec, self._clock()+2)
            return self._slot

    def claim(self) -> ControlWriteTicket | None:
        with self._lock:
            ticket = self._slot
            if self._closed or ticket is None or ticket.claimed or ticket.completion.done():
                return None
            if self._clock() >= ticket.deadline:
                ticket.completion.set_result(ControlWriteResult('timeout'))
                return None
            ticket.claimed = True
            return ticket

    def cancel(self, ticket: ControlWriteTicket) -> None:
        with self._lock:
            if self._slot is ticket:
                ticket.completion.cancel()
                if not ticket.claimed:
                    self._slot = None

    def finish(self, ticket: ControlWriteTicket, result: ControlWriteResult) -> None:
        with self._lock:
            if self._slot is ticket:
                if not ticket.completion.done():
                    ticket.completion.set_result(result)
                elif ticket.completion.cancelled():
                    self._slot = None

    def take(self, ticket: ControlWriteTicket) -> ControlWriteResult | None:
        with self._lock:
            if self._slot is not ticket or not ticket.completion.done() or ticket.completion.cancelled():
                return None
            self._slot = None
            return ticket.completion.result()

    def close(self) -> None:
        with self._lock:
            self._closed = True
            if self._slot is not None and not self._slot.completion.done():
                self._slot.completion.set_result(ControlWriteResult('cancelled'))
