"""Simulate a camera-video discontinuity offline and independently decode the restart.

python -m scripts.validate_native_recovery CAPTURE --flow flow5 --drop-at 10 --drop-count 5
No KCP gap is skipped: only complete V1 records of a clean captured flow are used
to model a separately re-established transport. No camera connections or files.
"""

from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path

from backend.app.drivers.yoosee.p2p.v1_receive import V1Record
from backend.app.media.hevc_recovery import HevcRecovery

from .replay_native_media import replay
from .validate_native_decode import Sample, validate


class RecoveryAudit:
    def __init__(self, flow: str, drop_at: int, drop_count: int, max_frames: int = 120) -> None:
        if not 1 <= drop_at <= 10000 or not 1 <= drop_count <= 10000 or not 1 <= max_frames <= 120:
            raise ValueError("invalid bounded recovery scenario")
        self.sample = Sample(flow, "video")
        self.gate = HevcRecovery()
        self.drop_at, self.drop_count, self.max_frames = drop_at, drop_count, max_frames
        self.index = self.discarded = 0
        self.resumed_at: int | None = None
        self._lost_timestamp: int | None = None
        self.resume_delta: int | None = None

    def consume(self, flow: str, record: V1Record) -> None:
        if flow != self.sample.flow:
            return
        if record.encoding is not None:
            self.sample.consume(flow, record)
            self.gate.discontinuity()
            return
        if not record.video:
            return
        self.index += 1
        if self.index == self.drop_at:
            self.gate.discontinuity()
            self._lost_timestamp = record.video_timestamp
        if self.drop_at <= self.index < self.drop_at + self.drop_count:
            return
        if self.sample.frames >= self.max_frames:
            return
        decision = self.gate.inspect(record.video, record.video_timestamp)
        if self.index < self.drop_at:
            return
        if not decision.emit:
            self.discarded += 1
            return
        if self.resumed_at is None:
            if not decision.restart_decoder:
                raise ValueError("recovery did not request a new decoder")
            self.resumed_at = self.index
            self.resume_delta = record.video_timestamp - (self._lost_timestamp or 0)
        elif decision.restart_decoder:
            raise ValueError("another decoder epoch occurred within the sample")
        self.sample.consume(flow, record)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("capture", type=Path)
    parser.add_argument("--flow", required=True)
    parser.add_argument("--drop-at", type=int, required=True)
    parser.add_argument("--drop-count", type=int, required=True)
    args = parser.parse_args()
    try:
        audit = RecoveryAudit(args.flow, args.drop_at, args.drop_count)
        report = replay(args.capture, audit.consume)
        selected = next((flow for flow in report["flows"] if flow["flow"] == args.flow), None)
        if (selected is None or selected["error"] or selected["media"]["records"]["error"]
                or selected["media"]["records"]["incomplete_tail_bytes"]):
            raise ValueError("recovery scenario requires a clean captured transport")
        if audit.sample.frames != audit.max_frames:
            raise ValueError("capture ended before the complete recovery sample")
        result = validate(audit.sample)
        result.update(resumed_at_frame=audit.resumed_at, dependent_frames_discarded=audit.discarded,
                      simulated_lost_frames=args.drop_count, resume_delta_ticks=audit.resume_delta,
                      scenario="offline_discontinuity_fresh_decoder")
        print(json.dumps(result, indent=2))
        if not all(result[key] for key in ("strict_decode_ok", "header_matches", "frame_count_matches")):
            raise SystemExit(2)
    except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
        raise SystemExit(f"Recovery audit failed: {type(exc).__name__}") from None


if __name__ == "__main__":
    main()
