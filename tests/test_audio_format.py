from __future__ import annotations

import struct

import pytest

from backend.app import audio_format


def test_canonical_audio_format_is_sixteen_khz_in_complete_twenty_ms_frames() -> None:
    assert audio_format.PCM_SAMPLE_RATE == 16_000
    assert audio_format.PCM_FRAME_BYTES == 640
    assert audio_format.MAX_PCM_BYTES == 320_000
    assert audio_format.duration_ms(bytes(1_280)) == 40


def test_downsample_to_8khz_averages_pairs_and_preserves_duration() -> None:
    source = struct.pack("<hhhh", 1_000, 3_000, -3_000, -1_000)
    assert audio_format.downsample_to_8khz(source) == struct.pack("<hh", 2_000, -2_000)


def test_downsample_rejects_partial_sample_pairs() -> None:
    with pytest.raises(ValueError, match="pairs"):
        audio_format.downsample_to_8khz(bytes(6))
