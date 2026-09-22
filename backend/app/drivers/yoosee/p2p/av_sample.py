"""Ephemeral, bounded HEVC sample for diagnostics; no files, sockets or decoder."""

from ....media.hevc_recovery import HevcRecovery
from .kcp_receive import ReceiveError
from .stream_protocol import V1EncodingHeader
from .v1_receive import V1Record

MAX_BYTES = 2 * 1024 * 1024
MAX_FRAMES = 120


class AvVideoSample:
    """Caller owns successful samples and must close them after validation.

    Only one continuous decoder epoch is retained. Audio is never copied. Clearing
    releases references, not a guarantee of secure erasure from Python memory.
    """

    flow = "live-diagnostic"
    kind = "video"

    def __init__(self) -> None:
        self.data = bytearray()
        self.encoding: V1EncodingHeader | None = None
        self.frames = 0
        self.discarded = 0
        self.closed = False
        self._gate = HevcRecovery()
        self._first_timestamp: int | None = None
        self._last_timestamp: int | None = None

    @property
    def timestamp_span_ticks(self) -> int:
        if self._first_timestamp is None or self._last_timestamp is None:
            return 0
        return self._last_timestamp - self._first_timestamp

    def close(self) -> None:
        self.data.clear()
        self.encoding = None
        self.frames = self.discarded = 0
        self._gate.discontinuity()
        self._first_timestamp = self._last_timestamp = None
        self.closed = True

    def consume(self, record: V1Record) -> None:
        try:
            self._consume(record)
        except (ValueError, ReceiveError):
            self.close()
            raise ReceiveError("native video sample rejected") from None

    def _consume(self, record: V1Record) -> None:
        if self.closed:
            raise ValueError("sample closed")
        if record.encoding is not None:
            encoding = record.encoding
            if (encoding.video_codec != 5 or not 0 < encoding.video_width <= 1920
                    or not 0 < encoding.video_height <= 1080
                    or (self.encoding is not None and self.encoding != encoding)):
                raise ValueError("unsupported or changing diagnostic configuration")
            self.encoding = encoding
        if not record.video or self.frames >= MAX_FRAMES:
            return
        if self.encoding is None:
            raise ValueError("missing sample configuration")
        decision = self._gate.inspect(record.video, record.video_timestamp)
        if not decision.emit:
            if self.frames or decision.reason != "waiting_for_configured_idr":
                raise ValueError("sample discontinuity")
            self.discarded += 1
            return
        if self.frames and decision.restart_decoder:
            raise ValueError("sample epoch changed")
        if len(self.data) + len(record.video) > MAX_BYTES:
            raise ValueError("sample budget exceeded")
        self.data.extend(record.video)
        if self._first_timestamp is None:
            self._first_timestamp = record.video_timestamp
        self._last_timestamp = record.video_timestamp
        self.frames += 1
