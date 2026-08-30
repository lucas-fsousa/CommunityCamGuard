"""Bounded streaming AMR-NB mode-7 encoder used by the legacy talk channel."""

from __future__ import annotations

import ctypes
import ctypes.util

SAMPLE_RATE = 8_000
SAMPLES_PER_FRAME = 160
PCM_FRAME_BYTES = SAMPLES_PER_FRAME * 2
ENCODED_FRAME_BYTES = 32
MODE_7 = 7


class AmrNbEncoder:
    """Keep one native encoder state for a bounded push-to-talk utterance."""

    def __init__(self, *, max_seconds: float = 10.0, library: str | None = None) -> None:
        if not 0.1 <= max_seconds <= 60.0:
            raise ValueError("AMR-NB session limit must be between 0.1 and 60 seconds")
        name = library or ctypes.util.find_library("opencore-amrnb")
        if not name:
            raise RuntimeError("libopencore-amrnb is unavailable")
        self._codec = ctypes.CDLL(name)
        self._codec.Encoder_Interface_init.argtypes = (ctypes.c_int,)
        self._codec.Encoder_Interface_init.restype = ctypes.c_void_p
        self._codec.Encoder_Interface_Encode.argtypes = (
            ctypes.c_void_p,
            ctypes.c_int,
            ctypes.POINTER(ctypes.c_int16),
            ctypes.POINTER(ctypes.c_ubyte),
            ctypes.c_int,
        )
        self._codec.Encoder_Interface_Encode.restype = ctypes.c_int
        self._codec.Encoder_Interface_exit.argtypes = (ctypes.c_void_p,)
        self._codec.Encoder_Interface_exit.restype = None
        self._state = self._codec.Encoder_Interface_init(0)
        if not self._state:
            raise RuntimeError("AMR-NB encoder initialization failed")
        self._maximum_bytes = int(max_seconds * SAMPLE_RATE) * 2
        self._received_bytes = 0
        self._pending = bytearray()
        self._closed = False

    def __enter__(self) -> AmrNbEncoder:
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()

    def close(self) -> None:
        if self._closed:
            return
        self._codec.Encoder_Interface_exit(self._state)
        self._state = None
        self._pending.clear()
        self._closed = True

    def feed(self, pcm16le: bytes, *, final: bool = False) -> tuple[bytes, ...]:
        """Consume signed 8 kHz mono PCM and return every complete 20 ms AMR frame."""

        if self._closed:
            raise RuntimeError("AMR-NB encoder is closed")
        if len(pcm16le) % 2:
            raise ValueError("PCM16 input must contain complete little-endian samples")
        if self._received_bytes + len(pcm16le) > self._maximum_bytes:
            raise ValueError("AMR-NB input exceeds the configured session limit")
        self._received_bytes += len(pcm16le)
        self._pending.extend(pcm16le)
        if final and self._pending and len(self._pending) < PCM_FRAME_BYTES:
            self._pending.extend(bytes(PCM_FRAME_BYTES - len(self._pending)))

        frames: list[bytes] = []
        while len(self._pending) >= PCM_FRAME_BYTES:
            raw = bytes(self._pending[:PCM_FRAME_BYTES])
            del self._pending[:PCM_FRAME_BYTES]
            frames.append(self._encode_frame(raw))
        if final and self._pending:
            raise RuntimeError("AMR-NB finalization left a partial PCM frame")
        return tuple(frames)

    def _encode_frame(self, pcm16le: bytes) -> bytes:
        samples = (ctypes.c_int16 * SAMPLES_PER_FRAME).from_buffer_copy(pcm16le)
        output = (ctypes.c_ubyte * 64)()
        size = self._codec.Encoder_Interface_Encode(
            self._state,
            MODE_7,
            samples,
            output,
            0,
        )
        if size != ENCODED_FRAME_BYTES or (output[0] >> 3) & 0x0F != MODE_7:
            raise RuntimeError("AMR-NB encoder returned an unexpected mode-7 frame")
        return bytes(output[:size])


def encode_pcm16le(pcm16le: bytes, *, max_seconds: float = 10.0) -> tuple[bytes, ...]:
    """Encode one bounded PCM buffer, padding only its final 20 ms frame."""

    if not pcm16le:
        raise ValueError("AMR-NB input is empty")
    with AmrNbEncoder(max_seconds=max_seconds) as encoder:
        return encoder.feed(pcm16le, final=True)
