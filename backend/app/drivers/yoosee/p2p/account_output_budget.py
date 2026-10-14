"""Single-owner output accounting with one non-borrowable PTZ cleanup reserve."""

from __future__ import annotations


class OutputExhausted(ValueError):
    pass


class AccountOutputBudget:
    """Attempts consume capacity before transport I/O, with no refunds on failure."""

    def __init__(self, packets: int = 512, octets: int = 1024 * 1024):
        if (type(packets) is not int or not 1 <= packets <= 512
                or type(octets) is not int or not 1 <= octets <= 1024 * 1024):
            raise ValueError("invalid account output limits")
        self._packets, self._octets = packets, octets
        self._ptz: PtzOutputReserve | None = None

    def charge(self, size: int) -> None:
        if type(size) is not int or not 0 < size <= 1024 * 1024:
            raise ValueError("invalid datagram size")
        reserved = self._ptz.remaining if self._ptz else 0
        if self._packets - reserved < 1 or self._octets - reserved * 4096 < size:
            raise OutputExhausted("account output budget exhausted")
        self._packets -= 1
        self._octets -= size

    def reserve_ptz(self) -> PtzOutputReserve:
        # One START, three identical RELEASE attempts and two receipt sends.
        if self._ptz is not None or self._packets < 6 or self._octets < 6 * 4096:
            raise OutputExhausted("PTZ cleanup capacity unavailable")
        self._ptz = PtzOutputReserve(self)
        return self._ptz


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
        self._slots[kind] -= 1
        self._owner._packets -= 1
        self._owner._octets -= size

    def close(self) -> None:
        if self._owner._ptz is self:
            self._owner._ptz = None
        self._slots = {"start": 0, "release": 0, "receipt": 0}
