"""Canonical PCM format shared by the HTTP boundary and camera drivers."""

from __future__ import annotations

import struct

PCM_SAMPLE_RATE = 16_000
PCM_CHANNELS = 1
PCM_SAMPLE_BYTES = 2
PCM_FRAME_MS = 20
PCM_SAMPLES_PER_FRAME = PCM_SAMPLE_RATE * PCM_FRAME_MS // 1_000
PCM_FRAME_BYTES = PCM_SAMPLES_PER_FRAME * PCM_SAMPLE_BYTES
MAX_PCM_SECONDS = 10
MAX_PCM_BYTES = PCM_SAMPLE_RATE * PCM_SAMPLE_BYTES * MAX_PCM_SECONDS


def duration_ms(pcm16le: bytes | bytearray) -> int:
    """Return the duration of canonical mono PCM, rejecting partial samples."""

    if len(pcm16le) % PCM_SAMPLE_BYTES:
        raise ValueError("PCM input must contain complete signed 16-bit samples")
    return len(pcm16le) * 1_000 // (PCM_SAMPLE_RATE * PCM_SAMPLE_BYTES)


def downsample_to_8khz(pcm16le: bytes) -> bytes:
    """Convert canonical 16 kHz PCM to AMR-NB input without changing frame duration."""

    if len(pcm16le) % 4:
        raise ValueError("16 kHz PCM must contain complete pairs of samples")
    output = bytearray(len(pcm16le) // 2)
    for index, (first, second) in enumerate(struct.iter_unpack("<hh", pcm16le)):
        # Averaging adjacent samples is a small low-pass filter and avoids the aliasing caused by
        # simply dropping every other sample.  It also preserves exact 20 ms frame boundaries.
        struct.pack_into("<h", output, index * 2, (first + second) // 2)
    return bytes(output)
