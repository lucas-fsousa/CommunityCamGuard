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
            "max_send_ms=%.1f elapsed_ms=%.1f opened=%s released=%s",
            safe_id, self.packets, self.late_packets, self.max_lateness_ms,
            self.max_send_ms, max(0.0, elapsed * 1000), opened, released,
        )
