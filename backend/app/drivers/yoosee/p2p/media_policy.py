"""Explicit media lifetime and constant-space rate budgets; no network or retries."""

import math
import time
from collections.abc import Callable
from enum import Enum


class MediaPolicy(Enum):
    FINITE = "finite"
    CONTINUOUS = "continuous"


def validate_media_policy(policy: MediaPolicy) -> None:
    if not isinstance(policy, MediaPolicy):
        raise ValueError("explicit media policy required")


class MediaRateBudget:
    """One second of burst credit, replenished at the same rate, never accumulated.

    Admission limits, not retained-buffer sizes or a camera bitrate assertion.
    RX2MiB/s, TX256KiB/s,4096 framed records/s. Exhaustion fails closed, never waits.
    """

    def __init__(self, *, clock: Callable[[], float] = time.monotonic):
        self._clock = clock
        self._last = clock()
        if not math.isfinite(self._last):
            raise ValueError("invalid media clock")
        self._limits = (2 * 1024 * 1024, 256 * 1024, 4096)
        self._credit = list(map(float, self._limits))

    def charge(self, *, rx: int = 0, tx: int = 0, records: int = 0) -> None:
        costs = (rx, tx, records)
        if any(type(cost) is not int or cost < 0 for cost in costs):
            raise ValueError("invalid media charge")
        now = self._clock()
        if not math.isfinite(now) or now < self._last:
            raise ValueError("invalid media clock")
        elapsed = min(1.0, now - self._last)
        self._last = now
        for index, limit in enumerate(self._limits):
            self._credit[index] = min(limit, self._credit[index] + elapsed * limit)
        if any(cost > credit for cost, credit in zip(costs, self._credit, strict=True)):
            raise ValueError("media rate exhausted")
        for index, cost in enumerate(costs):
            self._credit[index] -= cost
