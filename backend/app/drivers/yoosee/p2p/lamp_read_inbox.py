"""One lamp read/write slot; a claimed cancelled write stays reserved until owner retirement."""

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field

from .lamp_control_specs import LampWriteSpec, valid_target
from .lamp_read_job import LampReadResult, LampReadSpec


@dataclass(frozen=True, slots=True)
class LampReadTicket:
    generation: int
    serial: int


@dataclass(slots=True)
class _Slot:
    ticket: LampReadTicket
    spec: LampReadSpec | LampWriteSpec = field(repr=False)
    deadline: float
    claimed: bool = False
    result: LampReadResult | None = None
    abandoned: bool = False


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
        if not isinstance(spec, LampReadSpec) or not valid_target(spec.access_id, spec.device_id):
            raise ValueError("typed lamp-state read required")
        return self._submit(spec)

    def submit_write(self, spec: LampWriteSpec) -> LampReadTicket:
        if not isinstance(spec, LampWriteSpec):
            raise ValueError('typed binary lamp write required')
        return self._submit(spec)

    def _submit(self, spec: LampReadSpec | LampWriteSpec) -> LampReadTicket:
        with self._lock:
            if self._closed or self._slot is not None or self._serial >= 1 << 64:
                raise ValueError("lamp-state read admission unavailable")
            ticket = LampReadTicket(self._generation, self._serial)
            self._serial += 1
            self._slot = _Slot(ticket, spec, self._clock() + 2)
            return ticket

    def claim(self) -> tuple[LampReadTicket, LampReadSpec | LampWriteSpec, float] | None:
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
            return (self._closed or self._slot is None or self._slot.ticket != ticket
                    or self._slot.abandoned)

    def cancel(self, ticket: LampReadTicket) -> None:
        with self._lock:
            if self._slot is not None and self._slot.ticket == ticket:
                if self._slot.claimed and self._slot.result is None and isinstance(self._slot.spec, LampWriteSpec):
                    self._slot.abandoned = True  # Wait for the owner to retire the possible send.
                else:
                    self._slot = None

    def finish(self, ticket: LampReadTicket, result: LampReadResult) -> None:
        if not isinstance(result, LampReadResult):
            raise ValueError("typed lamp exchange result required")
        with self._lock:
            if (self._slot is not None and self._slot.ticket == ticket
                    and self._slot.claimed and self._slot.result is None):
                if self._slot.abandoned:
                    self._slot = None
                else:
                    self._slot.result = result

    def take(self, ticket: LampReadTicket) -> LampReadResult | None:
        with self._lock:
            slot = self._slot
            if slot is None or slot.ticket != ticket or slot.result is None or slot.abandoned:
                return None
            self._slot = None
            return slot.result

    def close(self) -> None:
        with self._lock:
            self._closed = True
            if self._slot is not None and self._slot.result is None:
                self._slot.result = LampReadResult("cancelled")
