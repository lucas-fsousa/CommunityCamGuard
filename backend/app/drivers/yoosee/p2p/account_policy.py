"""Explicit account lifetime and constant-space traffic admission; no I/O."""

import math
import time
from collections.abc import Callable
from enum import Enum


class AccountPolicy(Enum):
    FINITE = "finite"
    CONTINUOUS = "continuous"


def validate_policy(policy: AccountPolicy) -> None:
    if not isinstance(policy, AccountPolicy):
        raise ValueError("explicit account policy required")


class TrafficBudget:
    """Finite allowance or dual token bucket, never a growing packet history.

    Continuous mode permits the same initial burst, then replenishes that capacity
    over60seconds. Exhaustion fails closed; it does not queue, sleep or retry.
    Reservation capacity is withheld from normal admission across replenishment.
    """

    def __init__(self, packets: int = 512, octets: int = 1024 * 1024, *,
                 policy: AccountPolicy = AccountPolicy.FINITE,
                 clock: Callable[[], float] = time.monotonic):
        validate_policy(policy)
        if (type(packets) is not int or not 1 <= packets <= 512
                or type(octets) is not int or not 1 <= octets <= 1024 * 1024):
            raise ValueError("invalid traffic limits")
        self._max_packets, self._max_octets = packets, octets
        self._packets, self._octets = float(packets), float(octets)
        self._policy, self._clock = policy, clock
        self._last = clock()
        if not math.isfinite(self._last):
            raise ValueError("invalid traffic clock")

    def available(self, packets: int, octets: int) -> bool:
        now = self._clock()
        if not math.isfinite(now) or now < self._last:
            raise ValueError("invalid traffic clock")
        if self._policy is AccountPolicy.CONTINUOUS:
            elapsed = now - self._last
            self._packets = min(self._max_packets, self._packets + elapsed * self._max_packets / 60)
            self._octets = min(self._max_octets, self._octets + elapsed * self._max_octets / 60)
        self._last = now
        return self._packets >= packets and self._octets >= octets

    def charge(self, size: int, *, reserved: int = 0) -> None:
        if (type(size) is not int or not 0 <= size <= 1024 * 1024
                or type(reserved) is not int or not 0 <= reserved <= 8):
            raise ValueError("invalid traffic charge")
        if not self.available(1 + reserved, size + reserved * 4096):
            raise ValueError("traffic budget exhausted")
        self._packets -= 1
        self._octets -= size
