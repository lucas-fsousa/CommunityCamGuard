"""Constant-space arrival timing; no payloads, source clocks or background work."""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ReceptionTiming:
    ready_ms: int | None
    first_video_ms: int | None
    max_video_gap_ms: int
    trailing_video_gap_ms: int
    observation_ms: int


class ArrivalTiming:
    """Local monotonic arrival gaps, not FPS, capture latency or decoder timing.

    Freeze before initiating CLOSE so teardown time cannot inflate media stalls.
    Multiple records in one socket batch share a timestamp; do not infer their
    capture spacing. Storage remains constant for arbitrarily many records.
    """

    def __init__(self, started: float):
        self.started = started
        self.ready_at: float | None = None
        self.first_video: float | None = None
        self.last_video: float | None = None
        self.max_gap = 0.0
        self.result: ReceptionTiming | None = None

    def ready(self, now: float) -> None:
        if self.result is None and self.ready_at is None:
            self.ready_at = now

    def video(self, now: float) -> None:
        if self.result is not None:
            return
        if self.first_video is None:
            self.first_video = now
        if self.last_video is not None:
            self.max_gap = max(self.max_gap, now - self.last_video)
        self.last_video = now

    def finish(self, now: float) -> ReceptionTiming:
        if self.result is None:
            def elapsed(value: float | None) -> int | None:
                return None if value is None else round(max(0.0, value - self.started) * 1000)

            trailing = 0.0 if self.last_video is None else max(0.0, now - self.last_video)
            self.result = ReceptionTiming(elapsed(self.ready_at), elapsed(self.first_video),
                round(self.max_gap * 1000), round(trailing * 1000),
                round(max(0.0, now - self.started) * 1000))
        return self.result
