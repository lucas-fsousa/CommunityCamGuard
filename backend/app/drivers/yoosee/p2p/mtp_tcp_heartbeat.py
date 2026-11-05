"""Bounded caller-driven MTP maintenance on the already measured TCP route."""

import logging
import math
from collections.abc import Callable

from .mtp_tcp_measurement import build_mtp_tcp_measurement, matches_mtp_tcp_measurement_ack

log = logging.getLogger(__name__)


class TcpMeterHeartbeat:
    """One pending probe, no catch-up burst, socket, login or AV progress credit.

    The1s cadence/3s receipt window are bounded local policy, not the SDK's
    adaptive multi-route scoring scheduler. The initial sequence1 is not reused.
    """

    def __init__(self, *, link_id: int, source_id: int, destination_id: int, call_id: int,
                 clock: Callable[[], float]):
        self._link, self._source, self._destination, self._call = link_id, source_id, destination_id, call_id
        self._clock = clock
        self._last = clock()
        if not math.isfinite(self._last) or self._last < 0:
            raise ValueError('invalid TCP maintenance clock')
        self._next = self._last + 1
        self._sequence = 1
        self._pending: tuple[int, int, float] | None = None
        self.confirmed = 0
        self.closed = False

    def _now(self) -> float:
        now = self._clock()
        if not math.isfinite(now) or now < self._last:
            raise ValueError('invalid TCP maintenance clock')
        self._last = now
        return now

    def due(self) -> bytes | None:
        if self.closed:
            raise ValueError('TCP maintenance is closed')
        now = self._now()
        if now < self._next or (self._pending is not None and now - self._pending[2] < 3):
            return None
        self._sequence = (self._sequence + 1) & 0xFFFFFFFF
        tick = int(now * 1000)
        wire = build_mtp_tcp_measurement(relay_link_id=self._link, source_id=self._source,
            destination_id=self._destination, call_id=self._call, sequence=self._sequence,
            timestamp_ms=tick, session_role=1)
        self._pending = self._sequence, tick, now
        self._next = now + 1
        return wire

    def observe(self, frame: bytes) -> bool:
        if self.closed or self._pending is None:
            return False
        sequence, tick, sent = self._pending
        if self._now() - sent >= 3 or not matches_mtp_tcp_measurement_ack(frame,
                expected_link_id=self._link, expected_source_id=self._destination,
                expected_destination_id=self._source, expected_sequence=sequence,
                expected_timestamp_ms=tick):
            return False
        self._pending = None
        self.confirmed = min(0xFFFFFFFF, self.confirmed + 1)
        if self.confirmed == 1:
            log.warning('native TCP periodic meter acknowledged sequence=%d', sequence)
        return True

    def close(self) -> None:
        self.closed = True
        self._pending = None
