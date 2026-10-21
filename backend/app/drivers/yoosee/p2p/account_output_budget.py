"""Single-owner output accounting with one non-borrowable PTZ cleanup reserve."""

from __future__ import annotations

import time
from collections.abc import Callable

from .account_policy import AccountPolicy, TrafficBudget


class OutputExhausted(ValueError):
    pass


class AccountOutputBudget:
    """Attempts consume capacity before transport I/O, with no refunds on failure."""

    def __init__(self, packets: int = 512, octets: int = 1024 * 1024, *,
                 policy: AccountPolicy = AccountPolicy.FINITE,
                 clock: Callable[[], float] = time.monotonic):
        self._traffic = TrafficBudget(packets, octets, policy=policy, clock=clock)
        self._ptz: PtzOutputReserve | None = None
        self._media: MediaOutputReserve | None = None

    def charge(self, size: int) -> None:
        if type(size) is not int or not 0 < size <= 1024 * 1024:
            raise ValueError("invalid datagram size")
        reserved = self._reserved
        try:
            self._traffic.charge(size, reserved=reserved)
        except ValueError as exc:
            raise OutputExhausted("account output budget exhausted") from exc

    def reserve_ptz(self) -> PtzOutputReserve:
        # One START, three identical RELEASE attempts and two receipt sends.
        required = self._reserved + 6
        if self._ptz is not None or not self._traffic.available(required, required * 4096):
            raise OutputExhausted("PTZ cleanup capacity unavailable")
        self._ptz = PtzOutputReserve(self)
        return self._ptz

    @property
    def _reserved(self) -> int:
        return ((self._ptz.remaining if self._ptz else 0)
                + (self._media.remaining if self._media else 0))

    def reserve_media(self) -> MediaOutputReserve:
        required = self._reserved + 2
        if self._media is not None or not self._traffic.available(required, required * 4096):
            raise OutputExhausted("media cleanup capacity unavailable")
        self._media = MediaOutputReserve(self)
        return self._media


class PtzOutputReserve:
    """No queue or I/O. Only the sole worker may debit/release this reservation.

    close() relinquishes unused capacity, not already charged attempts; it is not
    physical STOP confirmation. Caller must complete cleanup before admitting a
    new motion. Receipt capacity is finite, not an unlimited acknowledgment path.
    """

    def __init__(self, owner: AccountOutputBudget):
        self._owner = owner
        self._slots = {"start": 1, "release": 3, "receipt": 2}

    @property
    def remaining(self) -> int:
        return sum(self._slots.values())

    def charge(self, kind: str, size: int) -> None:
        if (self._owner._ptz is not self or kind not in self._slots
                or not self._slots[kind] or type(size) is not int or not 0 < size <= 4096):
            raise OutputExhausted("PTZ output reservation unavailable")
        self._owner._traffic.charge(size)
        self._slots[kind] -= 1

    def close(self) -> None:
        if self._owner._ptz is self:
            self._owner._ptz = None
        self._slots = {"start": 0, "release": 0, "receipt": 0}


class MediaOutputReserve:
    """One route OPEN and one CLOSE; no retransmission or borrowed PTZ capacity."""

    def __init__(self, owner: AccountOutputBudget):
        self._owner = owner
        self._slots = {"open": 1, "close": 1}

    @property
    def remaining(self) -> int:
        return sum(self._slots.values())

    def charge(self, kind: str, size: int) -> None:
        if (self._owner._media is not self or kind not in self._slots
                or not self._slots[kind] or type(size) is not int or not 0 < size <= 4096):
            raise OutputExhausted("media output reservation unavailable")
        self._owner._traffic.charge(size)
        self._slots[kind] -= 1

    def close(self) -> None:
        if self._owner._media is self:
            self._owner._media = None
        self._slots = {"open": 0, "close": 0}
