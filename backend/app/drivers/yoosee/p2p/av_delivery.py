"""Bounded AAC handoff sharing the native video receiver, not another camera socket."""

from collections import deque
from dataclasses import dataclass, field
from threading import Lock

from .stream_protocol import V1EncodingHeader
from .v1_receive import V1Record
from .video_delivery import VideoDelivery

MAX_AUDIO_BYTES = 256 * 1024
MAX_AUDIO_FRAMES = 128


@dataclass(frozen=True, slots=True)
class AudioFrame:
    payload: bytes = field(repr=False)
    timestamp: int


def supported_audio(header: V1EncodingHeader) -> bool:
    # Actual camera-3 capture: AAC-LC ADTS, 16 kHz, mono, 1024 samples/frame.
    # Other variants need their own evidence, not a brand-wide capability guess.
    return (header.audio_codec == 4 and header.audio_codec_option == 2
            and header.audio_channels == 1 and header.audio_sample_rate == 16000
            and header.audio_frame_size == 1024 and header.audio_bit_width == 16)


class NativeAvDelivery(VideoDelivery):
    def __init__(self) -> None:
        super().__init__()
        self._audio_lock = Lock()
        self._audio: deque[AudioFrame] = deque()
        self._audio_bytes = 0
        self._audio_header: V1EncodingHeader | None = None
        self.audio_failure = ""

    def consume(self, record: V1Record) -> None:
        super().consume(record)
        # VideoDelivery's lock is never nested under this lock.
        with self._audio_lock:
            if self.closed:
                return
            if record.encoding is not None:
                if not supported_audio(record.encoding):
                    self.audio_failure = "unsupported_header"
                    raise ValueError("native AAC format not validated")
                self._audio_header = record.encoding
            if self._audio_header is None:
                return
            for index, payload in enumerate(record.audio):
                if (len(payload) < 7 or payload[0] != 255 or payload[1] & 0xF6 != 0xF0
                        or payload[2] >> 6 != 1 or (payload[2] >> 2) & 15 != 8
                        or ((payload[2] & 1) << 2 | payload[3] >> 6) != 1
                        or ((payload[3] & 3) << 11 | payload[4] << 3 | payload[5] >> 5) != len(payload)
                        or (not payload[1] & 1 and len(payload) < 9)):
                    self.audio_failure = "invalid_adts"
                    raise ValueError("bounded native ADTS frame required")
                if len(self._audio) >= MAX_AUDIO_FRAMES or self._audio_bytes + len(payload) > MAX_AUDIO_BYTES:
                    # Fail this source instead of emitting an undisclosed audio discontinuity.
                    self.audio_failure = "queue_overflow"
                    raise ValueError("native audio consumer overflow")
                self._audio.append(AudioFrame(payload, record.audio_timestamp + index * 64000))
                self._audio_bytes += len(payload)

    def take_audio(self) -> AudioFrame | None:
        with self._audio_lock:
            if not self._audio:
                return None
            frame = self._audio.popleft()
            self._audio_bytes -= len(frame.payload)
            return frame

    def close(self) -> None:
        super().close()
        with self._audio_lock:
            self._audio.clear()
            self._audio_bytes = 0
            self._audio_header = None
