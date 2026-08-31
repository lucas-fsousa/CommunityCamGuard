"""Content-free level diagnostics for fixed-format PCM camera audio."""

from __future__ import annotations

import math
import struct


class PcmLevelAccumulator:
    """Accumulate level statistics without retaining or logging audio samples."""

    def __init__(self) -> None:
        self._samples = 0
        self._nonzero = 0
        self._peak = 0
        self._sum_squares = 0

    def feed(self, pcm16le: bytes) -> None:
        if len(pcm16le) % 2:
            raise ValueError("PCM diagnostics require complete signed 16-bit samples")
        for (sample,) in struct.iter_unpack("<h", pcm16le):
            magnitude = abs(sample)
            self._samples += 1
            self._nonzero += int(sample != 0)
            self._peak = max(self._peak, magnitude)
            self._sum_squares += sample * sample

    def public(self) -> dict[str, int | float]:
        if not self._samples:
            return {
                "samples": 0,
                "peak_percent": 0.0,
                "rms_dbfs": -120.0,
                "nonzero_percent": 0.0,
            }
        rms = math.sqrt(self._sum_squares / self._samples)
        return {
            "samples": self._samples,
            "peak_percent": round(self._peak * 100 / 32768, 2),
            "rms_dbfs": round(20 * math.log10(rms / 32768), 2) if rms else -120.0,
            "nonzero_percent": round(self._nonzero * 100 / self._samples, 2),
        }
