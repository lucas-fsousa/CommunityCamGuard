from __future__ import annotations

import pytest

from backend.app.drivers.yoosee.p2p.aac_lc import (
    OUTPUT_BIT_RATE,
    extract_adts_frames,
    validate_aac_lc_adts_frame,
)


def _adts(payload: bytes = b"aac") -> bytes:
    length = 7 + len(payload)
    return (
        bytes(
            (
                0xFF,
                0xF1,
                0x60,
                0x40 | ((length >> 11) & 0x03),
                (length >> 3) & 0xFF,
                ((length & 0x07) << 5) | 0x1F,
                0xFC,
            )
        )
        + payload
    )


def test_encoder_bitrate_matches_native_audio_input() -> None:
    assert OUTPUT_BIT_RATE == 40_000


def test_extracts_complete_mpeg4_aac_lc_frames_incrementally() -> None:
    first = _adts(b"one")
    second = _adts(b"two-two")
    buffer = bytearray(first + second[:5])

    assert extract_adts_frames(buffer) == (first,)
    assert buffer == second[:5]
    buffer.extend(second[5:])
    assert extract_adts_frames(buffer, final=True) == (second,)
    assert buffer == bytearray()


def test_rejects_rx_mpeg2_and_wrong_aac_parameters() -> None:
    frame = _adts()
    validate_aac_lc_adts_frame(frame)
    with pytest.raises(ValueError, match="MPEG-4"):
        validate_aac_lc_adts_frame(frame[:1] + b"\xf9" + frame[2:])
    with pytest.raises(ValueError, match="16 kHz"):
        validate_aac_lc_adts_frame(frame[:2] + b"\x50" + frame[3:])


def test_rejects_truncated_or_corrupt_adts_stream() -> None:
    frame = _adts(b"complete")
    with pytest.raises(ValueError, match="truncated"):
        extract_adts_frames(bytearray(frame[:-1]), final=True)
    damaged = bytearray(frame)
    damaged[0] = 0
    with pytest.raises(ValueError, match="unexpected ADTS"):
        extract_adts_frames(damaged)
