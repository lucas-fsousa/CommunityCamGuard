"""Conservative, transport-neutral HEVC restart gate. No sockets or frame queue.

After any discontinuity, require an IDR with fresh VPS/SPS/PPS in that same complete
access unit. Never synthesize a keyframe or reuse parameter bytes across sessions.
Only the independently tested single-layer profile is intended for initial use.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

from .hevc_access import inspect_access_unit


@dataclass(frozen=True, slots=True)
class VideoDecision:
    emit: bool
    restart_decoder: bool
    epoch: int
    reason: str


class HevcRecovery:
    def __init__(self) -> None:
        self.waiting = True
        self.epoch = 0
        self._last_timestamp: int | None = None
        self._configuration: tuple[bytes, ...] | None = None

    def discontinuity(self) -> None:
        self.waiting = True
        self._last_timestamp = None
        self._configuration = None

    def _drop(self, reason: str) -> VideoDecision:
        self.discontinuity()
        return VideoDecision(False, False, self.epoch, reason)

    def inspect(self, payload: bytes, timestamp: int) -> VideoDecision:
        if type(timestamp) is not int or not 0 <= timestamp <= 0xFFFFFFFFFFFFFFFF:
            return self._drop("invalid_timestamp")
        try:
            units = inspect_access_unit(payload)
        except ValueError:
            return self._drop("invalid_access_unit")
        video = [nal for nal in units if nal.kind < 32]
        if not video:
            return self._drop("no_picture")
        if self._last_timestamp is not None and timestamp < self._last_timestamp:
            self.discontinuity()
        params = [nal for nal in units if nal.kind in (32, 33, 34)]
        complete = ([nal.kind for nal in params] == [32, 33, 34]
                    and all(nal.kind >= 32 for nal in units[:units.index(params[-1]) + 1])) if params else False
        idr = all(nal.kind in (19, 20) for nal in video)
        if params and (not complete or not idr):
            return self._drop("incomplete_or_unpaired_configuration")
        config = tuple(hashlib.sha256(nal.data).digest() for nal in params) if complete else None
        if config is not None and self._configuration is not None and config != self._configuration:
            self.waiting = True
        if self.waiting and not (idr and complete):
            return VideoDecision(False, False, self.epoch, "waiting_for_configured_idr")
        restart = self.waiting
        if restart:
            self.epoch += 1
        self.waiting = False
        self._last_timestamp = timestamp
        if config is not None:
            self._configuration = config
        return VideoDecision(True, restart, self.epoch, "configured_idr" if restart else "continuous")
