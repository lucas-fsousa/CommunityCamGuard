"""One-slot, generation-bound lamp-read handoff; cancellation never invokes a camera action."""

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field

from .lamp_read_job import LampReadResult, LampReadSpec


@dataclass(frozen=True, slots=True)
class LampReadTicket:
    generation: int
    serial: int


@dataclass(slots=True)
class _Slot:
    ticket: LampReadTicket
    spec: LampReadSpec = field(repr=False)
    deadline: float
    claimed: bool = False
    result: LampReadResult | None = None


class LampReadInbox:
    def __init__(self, generation: int, *, clock: Callable[[], float] = time.monotonic):
        if type(generation) is not int or not 0 < generation < 1 << 64:
            raise ValueError("lamp-read generation required")
        self._generation, self._clock = generation, clock
        self._lock = threading.Lock()
        self._serial = 0
        self._slot: _Slot | None = None
        self._closed = False

    def submit(self, spec: LampReadSpec) -> LampReadTicket:
        if (not isinstance(spec, LampReadSpec) or type(spec.access_id) is not int
                or not 0 <= spec.access_id < 1 << 64 or type(spec.device_id) is not int
                or not 0 < spec.device_id < 1 << 64):
            raise ValueError("typed lamp-state read required")
        with self._lock:
            if self._closed or self._slot is not None or self._serial >= 1 << 64:
                raise ValueError("lamp-state read admission unavailable")
            ticket = LampReadTicket(self._generation, self._serial)
            self._serial += 1
            self._slot = _Slot(ticket, spec, self._clock() + 2)
            return ticket

    def claim(self) -> tuple[LampReadTicket, LampReadSpec, float] | None:
        with self._lock:
            slot = self._slot
            if slot is None or slot.claimed or slot.result is not None or self._closed:
                return None
            if self._clock() >= slot.deadline:
                slot.result = LampReadResult("timeout")
                return None
            slot.claimed = True
            return slot.ticket, slot.spec, slot.deadline

    def should_cancel(self, ticket: LampReadTicket) -> bool:
        with self._lock:
            return self._closed or self._slot is None or self._slot.ticket != ticket

    def cancel(self, ticket: LampReadTicket) -> None:
        with self._lock:
            if self._slot is not None and self._slot.ticket == ticket:
                self._slot = None  # read-only: no STOP/action cleanup is needed

    def finish(self, ticket: LampReadTicket, result: LampReadResult) -> None:
        if not isinstance(result, LampReadResult):
            raise ValueError("typed lamp-read result required")
        with self._lock:
            if (self._slot is not None and self._slot.ticket == ticket
                    and self._slot.claimed and self._slot.result is None):
                self._slot.result = result

    def take(self, ticket: LampReadTicket) -> LampReadResult | None:
        with self._lock:
            slot = self._slot
            if slot is None or slot.ticket != ticket or slot.result is None:
                return None
            self._slot = None
            return slot.result

    def close(self) -> None:
        with self._lock:
            self._closed = True
            if self._slot is not None and self._slot.result is None:
                self._slot.result = LampReadResult("cancelled")
