"""Constant-memory, content-free RTSP delivery timing; not speaker telemetry."""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass

log = logging.getLogger(__name__)


@dataclass(slots=True)
class DeliveryTiming:
    packets: int = 0
    late_packets: int = 0
    max_lateness_ms: float = 0.0
    max_send_ms: float = 0.0
    refresh_calls: int = 0
    refresh_failures: int = 0
    max_refresh_ms: float = 0.0
    write_calls: int = 0
    write_failures: int = 0
    max_write_ms: float = 0.0

    def refresh(self, *, started: float, finished: float, succeeded: bool) -> None:
        self.refresh_calls += 1
        self.refresh_failures += int(not succeeded)
        self.max_refresh_ms = max(self.max_refresh_ms, (finished - started) * 1000)

    def write(self, *, started: float, finished: float, succeeded: bool) -> None:
        self.write_calls += 1
        self.write_failures += int(not succeeded)
        self.max_write_ms = max(self.max_write_ms, (finished - started) * 1000)

    def sent(self, *, started: float, finished: float, playback_deadline: float) -> None:
        """Measure successful writes relative to the nominal playback deadline.

        Counts include socket/control-refresh stalls, but cannot prove audible
        underflow or delivery to the speaker. No per-packet samples are retained.
        """
        lateness = max(0.0, (finished - playback_deadline) * 1000)
        self.packets += 1
        self.late_packets += int(lateness > 10.0)
        self.max_lateness_ms = max(self.max_lateness_ms, lateness)
        self.max_send_ms = max(self.max_send_ms, (finished - started) * 1000)

    def report(self, camera_id: str, *, elapsed: float, opened: bool, released: bool) -> None:
        safe_id = camera_id if re.fullmatch(r"cam_[0-9a-f]{24}", camera_id or "") else "unknown"
        log.info(
            "rtsp_audio_timing camera=%s packets=%d late_packets=%d max_lateness_ms=%.1f "
            "max_send_ms=%.1f elapsed_ms=%.1f opened=%s released=%s "
            "refresh_calls=%d refresh_failures=%d max_refresh_ms=%.1f "
            "write_calls=%d write_failures=%d max_write_ms=%.1f",
            safe_id, self.packets, self.late_packets, self.max_lateness_ms,
            self.max_send_ms, max(0.0, elapsed * 1000), opened, released,
            self.refresh_calls, self.refresh_failures, self.max_refresh_ms,
            self.write_calls, self.write_failures, self.max_write_ms,
        )
