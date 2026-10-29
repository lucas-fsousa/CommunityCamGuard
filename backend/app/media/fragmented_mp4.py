"""Bounded top-level framing for our local ffmpeg fragmented MP4 output.

This checks muxer framing/order, not arbitrary ISO BMFF payload semantics. A slow
consumer retires the output rather than silently losing dependent video fragments.
"""

from collections import deque
from dataclasses import dataclass, field

MAX_BYTES = 2 * 1024 * 1024
MAX_CHUNKS = 8


class Mp4OrderError(ValueError):
    """Fixed-label diagnostics only; never include arbitrary muxer/media bytes."""

    def __init__(self, expected: bytes, actual: bytes):
        super().__init__("unexpected MP4 box order")
        known = {b"ftyp", b"moov", b"moof", b"mdat", b"free", b"sidx", b"styp", b"mfra", b"mfro"}
        self.expected = expected.decode("ascii") if expected in known else "unknown"
        self.actual = actual.decode("ascii") if actual in known else "unknown"


@dataclass(frozen=True, slots=True)
class Mp4Chunk:
    initialization: bool
    data: bytes = field(repr=False)


class FragmentedMp4:
    def __init__(self) -> None:
        self._buffer = bytearray()
        self._pair = b""
        self._expected = b"ftyp"
        self._chunks: deque[Mp4Chunk] = deque()
        self._queued_bytes = 0
        self.started = False
        self._claimed = False
        self.closed = False

    def claim(self) -> None:
        """One decoder epoch owns this single-threaded collector, even before output."""
        if self.closed or self.started or self._claimed:
            raise ValueError("fresh MP4 output required")
        self._claimed = True

    @property
    def buffered_bytes(self) -> int:
        return len(self._buffer) + len(self._pair) + self._queued_bytes

    def feed(self, data: bytes) -> None:
        if self.closed:
            raise ValueError("MP4 output closed")
        try:
            if type(data) is not bytes or self.buffered_bytes + len(data) > MAX_BYTES:
                raise ValueError("MP4 output capacity exceeded")
            self.started = self.started or bool(data)
            self._buffer.extend(data)
            # Caller reads at most16KiB per step; parsing also has a finite box budget.
            boxes = 0
            while len(self._buffer) >= 8:
                size = int.from_bytes(self._buffer[:4], "big")
                kind = bytes(self._buffer[4:8])
                if not 8 <= size <= MAX_BYTES:
                    raise ValueError("unsupported MP4 box size")
                if kind != self._expected:
                    raise Mp4OrderError(self._expected, kind)
                if len(self._buffer) < size:
                    return
                boxes += 1
                if boxes > 64:
                    raise ValueError("MP4 box budget exceeded")
                box = bytes(self._buffer[:size])
                del self._buffer[:size]
                if kind in (b"ftyp", b"moof"):
                    self._pair = box
                    self._expected = b"moov" if kind == b"ftyp" else b"mdat"
                else:
                    if len(self._chunks) >= MAX_CHUNKS:
                        raise ValueError("MP4 consumer stalled")
                    chunk = Mp4Chunk(kind == b"moov", self._pair + box)
                    self._pair = b""
                    self._chunks.append(chunk)
                    self._queued_bytes += len(chunk.data)
                    self._expected = b"moof"
        except Exception:
            self.close()
            raise

    def take(self) -> Mp4Chunk | None:
        if not self._chunks:
            return None
        chunk = self._chunks.popleft()
        self._queued_bytes -= len(chunk.data)
        return chunk

    def close(self) -> None:
        self.closed = True
        self._buffer.clear()
        self._pair = b""
        self._chunks.clear()
        self._queued_bytes = 0
