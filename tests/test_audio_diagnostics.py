from backend.app.audio_diagnostics import PcmLevelAccumulator


def test_pcm_levels_are_content_free_and_measure_silence() -> None:
    levels = PcmLevelAccumulator()
    levels.feed(bytes(320))

    assert levels.public() == {
        "samples": 160,
        "peak_percent": 0.0,
        "rms_dbfs": -120.0,
        "nonzero_percent": 0.0,
    }


def test_pcm_levels_accumulate_signed_little_endian_chunks() -> None:
    levels = PcmLevelAccumulator()
    levels.feed(bytes.fromhex("ff7f008001000000"))
    levels.feed(bytes.fromhex("ffff"))

    assert levels.public() == {
        "samples": 5,
        "peak_percent": 100.0,
        "rms_dbfs": -3.98,
        "nonzero_percent": 80.0,
    }
