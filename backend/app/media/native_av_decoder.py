"""One HEVC decoder/H264 encoder, copied AAC, and optional original HEVC/AAC archive."""

from pathlib import Path

from .av_transport import AvTransport
from .fragmented_mp4 import FragmentedMp4
from .hevc_decoder import HevcDecoder


class NativeAvDecoder:
    def __init__(self, *, origin_us: int, output: FragmentedMp4, archive: Path | None = None,
                 archive_segment_seconds: int = 60):
        self._origin = origin_us
        self._mux = AvTransport(origin_us)
        self._decoder = HevcDecoder(output=output, transport="mpegts", audio=True, archive=archive,
                                    archive_segment_seconds=archive_segment_seconds)

    @property
    def pending_bytes(self) -> int:
        return self._decoder.pending_bytes

    def offer(self, payload: bytes, *, timestamp: int | None = None) -> bool:
        if timestamp is None:
            raise ValueError("native video PTS required")
        if self.pending_bytes:
            return False
        return self._decoder.offer(self._mux.video(payload, timestamp))

    def offer_audio(self, payload: bytes, timestamp: int) -> bool:
        if timestamp < self._origin:
            return True  # discard pre-IDR audio, do not shift the new video epoch
        if self.pending_bytes:
            return False
        return self._decoder.offer(self._mux.audio(payload, timestamp))

    def step(self) -> None:
        self._decoder.step()

    def close(self) -> None:
        self._decoder.close()
