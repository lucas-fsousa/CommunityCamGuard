"""One-slot typed admission between trusted driver callers and the socket owner."""

import math
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field

from .ptz_protocol import DIRECTIONS
from .ptz_scheduler import PtzSpec, ScheduledPtzResult


@dataclass(frozen=True, slots=True)
class MotionTicket:
    generation: int
    serial: int


@dataclass(slots=True)
class _Slot:
    ticket: MotionTicket
    spec: PtzSpec = field(repr=False)
    deadline: float
    claimed: bool = False
    cancelled: bool = False
    result: ScheduledPtzResult | None = None


class PtzInbox:
    """One gesture including unread result; no wire I/O, thread or login.

    submit/cancel/take are caller operations; claim/finish belong to one worker.
    Admission validates syntax, NOT device authorization: caller must be trusted.
    Cancellation of a claimed gesture never publishes success before STOP drain.
    """

    def __init__(self, generation: int, *, clock: Callable[[], float] = time.monotonic):
        if type(generation) is not int or not 0 < generation < 1 << 64:
            raise ValueError("invalid motion generation")
        self._generation, self._clock = generation, clock
        self._lock = threading.Lock()
        self._serial = 0
        self._closed = False
        self._slot: _Slot | None = None

    def submit(self, spec: PtzSpec) -> MotionTicket:
        if (not isinstance(spec, PtzSpec)
                or type(spec.access_id) is not int or not 0 < spec.access_id < 1 << 64
                or type(spec.device_id) is not int or not 0 < spec.device_id < 1 << 64
                or not isinstance(spec.direction, str) or spec.direction not in DIRECTIONS
                or not isinstance(spec.reviewed_directions, frozenset)
                or not spec.reviewed_directions <= DIRECTIONS.keys()
                or spec.direction not in spec.reviewed_directions
                or type(spec.duration) not in (int, float) or not math.isfinite(spec.duration)
                or not 0.1 <= spec.duration <= 0.5):
            raise ValueError("invalid reviewed gesture")
        with self._lock:
            if self._closed or self._slot is not None or self._serial >= 1 << 64:
                raise ValueError("motion admission unavailable")
            ticket = MotionTicket(self._generation, self._serial)
            self._serial += 1
            self._slot = _Slot(ticket, spec, self._clock() + 1)
            return ticket

    @staticmethod
    def _cancel(slot: _Slot) -> None:
        slot.cancelled = True
        if not slot.claimed and slot.result is None:
            slot.result = ScheduledPtzResult("cancelled", False, 0, False, False)

    def claim(self) -> tuple[MotionTicket, PtzSpec] | None:
        with self._lock:
            slot = self._slot
            if slot is None or slot.claimed or slot.result is not None:
                return None
            if self._closed or self._clock() >= slot.deadline:
                self._cancel(slot)
                return None
            slot.claimed = True
            return slot.ticket, slot.spec

    def cancel(self, ticket: MotionTicket) -> bool:
        with self._lock:
            slot = self._slot
            if slot is None or slot.ticket != ticket or slot.result is not None:
                return False
            self._cancel(slot)
            return True

    def should_cancel(self, ticket: MotionTicket) -> bool:
        with self._lock:
            return (self._closed or self._slot is None or self._slot.ticket != ticket
                    or self._slot.cancelled)

    def finish(self, ticket: MotionTicket, result: ScheduledPtzResult) -> bool:
        if not isinstance(result, ScheduledPtzResult):
            raise ValueError("typed terminal motion result required")
        with self._lock:
            slot = self._slot
            if slot is None or slot.ticket != ticket or not slot.claimed or slot.result is not None:
                return False
            slot.result = result
            if result.start_attempted and not result.release_delivery_confirmed:
                self._closed = True
            return True

    def take(self, ticket: MotionTicket) -> ScheduledPtzResult | None:
        with self._lock:
            slot = self._slot
            if slot is None or slot.ticket != ticket or slot.result is None:
                return None
            self._slot = None
            return slot.result

    def close(self) -> None:
        with self._lock:
            self._closed = True
            if self._slot is not None:
                self._cancel(self._slot)
