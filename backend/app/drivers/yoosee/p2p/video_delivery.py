"""Bounded single-consumer native video handoff; no decoder, audio or network."""

import threading
from collections import deque
from dataclasses import dataclass, field

from ....media.hevc_recovery import HevcRecovery
from .stream_protocol import V1EncodingHeader
from .v1_receive import V1Record

MAX_BYTES = 2 * 1024 * 1024
MAX_FRAMES = 64


@dataclass(frozen=True, slots=True)
class VideoFrame:
    payload: bytes = field(repr=False)
    timestamp: int
    width: int
    height: int
    epoch: int
    restart_decoder: bool
    encoding: V1EncodingHeader = field(repr=False)


class VideoDelivery:
    """One source and one polling consumer; never wait for consumer capacity.

    On overflow clear the backlog and require a fresh configured IDR, rather than
    handing a decoder dependent frames after dropping their references. Source
    replacement needs a new instance. No user callbacks run under the queue lock.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._frames: deque[VideoFrame] = deque()
        self._bytes = 0
        self._dimensions: tuple[int, int] | None = None
        self._encoding: V1EncodingHeader | None = None
        self._gate = HevcRecovery()
        self._closed = False

    def _reset(self) -> None:
        self._frames.clear()
        self._bytes = 0
        self._gate.discontinuity()

    def consume(self, record: V1Record) -> None:
        with self._lock:
            if self._closed:
                return
            if record.encoding is not None:
                header = record.encoding
                if (header.video_codec != 5 or not 0 < header.video_width <= 1920
                        or not 0 < header.video_height <= 1080):
                    self._reset()
                    self._dimensions = None
                    self._encoding = None
                    return
                dimensions = (header.video_width, header.video_height)
                if dimensions != self._dimensions:
                    self._reset()
                self._dimensions = dimensions
                self._encoding = header
            if not record.video or self._dimensions is None or self._encoding is None:
                return
            size = len(record.video)
            if size > MAX_BYTES:
                self._reset()
                return
            if len(self._frames) >= MAX_FRAMES or self._bytes + size > MAX_BYTES:
                self._reset()
            decision = self._gate.inspect(record.video, record.video_timestamp)
            if not decision.emit:
                # A malformed/discontinuous frame also invalidates queued history.
                self._reset()
                return
            if decision.restart_decoder:
                self._frames.clear()
                self._bytes = 0
            self._frames.append(VideoFrame(record.video, record.video_timestamp,
                *self._dimensions, decision.epoch, decision.restart_decoder, self._encoding))
            self._bytes += size

    def take(self) -> VideoFrame | None:
        with self._lock:
            if not self._frames:
                return None
            frame = self._frames.popleft()
            self._bytes -= len(frame.payload)
            return frame

    @property
    def buffered_bytes(self) -> int:
        with self._lock:
            return self._bytes

    def resynchronize(self) -> None:
        """Discard dependent backlog and require a fresh IDR, retaining the receiver."""
        with self._lock:
            self._reset()

    @property
    def closed(self) -> bool:
        with self._lock:
            return self._closed

    def close(self) -> None:
        """Retire and discard private frame references, including on cancellation."""
        with self._lock:
            self._closed = True
            self._reset()
            self._dimensions = None
            self._encoding = None
