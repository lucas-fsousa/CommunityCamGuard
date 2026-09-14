"""Content-free summary of bounded V1 records; raw timestamps are never logged."""

from collections.abc import Callable

from backend.app.drivers.yoosee.p2p.v1_receive import V1Receiver, V1Record


class CapturedFrames:
    def __init__(self, on_record: Callable[[V1Record], None] | None = None) -> None:
        self.on_record = on_record
        self.receiver = V1Receiver()
        self.previous: dict[str, int] = {}
        self.report: dict = dict(headers=0, audio_frames=0, video_frames=0,
                                 audio_bytes=0, video_bytes=0, error=None,
                                 audio_delta_min=None, audio_delta_max=None,
                                 video_delta_min=None, video_delta_max=None,
                                 audio_regressions=0, video_regressions=0)

    def consume(self, payload: bytes) -> None:
        if self.report["error"]:
            return
        try:
            records = self.receiver.feed(payload)
        except ValueError as exc:
            self.report["error"] = str(exc)
            return
        for record in records:
            if self.on_record is not None:
                self.on_record(record)
            if record.encoding is not None:
                self.report["headers"] += 1
                continue
            self.report["audio_frames"] += len(record.audio)
            self.report["video_frames"] += bool(record.video)
            self.report["audio_bytes"] += sum(map(len, record.audio))
            self.report["video_bytes"] += len(record.video)
            for kind, present, timestamp in (
                ("audio", bool(record.audio), record.audio_timestamp),
                ("video", bool(record.video), record.video_timestamp),
            ):
                if not present:
                    continue
                if kind in self.previous:
                    delta = timestamp - self.previous[kind]
                    self.report[kind + "_regressions"] += delta < 0
                    for suffix, operation in (("min", min), ("max", max)):
                        key = kind + "_delta_" + suffix
                        old = self.report[key]
                        self.report[key] = delta if old is None else operation(old, delta)
                self.previous[kind] = timestamp

    def finish(self) -> dict:
        self.report["incomplete_tail_bytes"] = self.receiver.buffered_bytes
        self.receiver.close()
        return self.report
