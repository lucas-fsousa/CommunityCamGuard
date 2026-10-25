"""Passive tap remains bounded and saves only explicit private operator artifacts."""

import json

from backend.app.diagnostics.native_audio_sample import MAX_BYTES, MAX_FRAMES, NativeAudioSample
from backend.app.drivers.yoosee.p2p.stream_protocol import unpack_v1_encoding_header
from backend.app.drivers.yoosee.p2p.v1_receive import V1Record
from tests.test_captured_media import header


def test_audio_only_records_are_sampled_without_allocating_decoder_or_camera(tmp_path):
    sample = NativeAudioSample()
    sample.consume(V1Record(encoding=unpack_v1_encoding_header(header())))
    for i in range(MAX_FRAMES + 10):
        sample.consume(V1Record(audio=(b"\xff\xf1\x50\x80\x00\x00\x00",), audio_timestamp=i))
    report = sample.save(tmp_path)
    assert report["frames"] == MAX_FRAMES and report["adts_sync"] is True
    assert report["encoding"]["audio_codec"] == 4
    assert len((tmp_path / "native-audio.raw").read_bytes()) <= MAX_BYTES
    sample.consume(V1Record(audio=(b"private",)))
    assert sample._audio_bytes == 0 and not sample._audio
    assert len(json.loads((tmp_path / "native-audio.json").read_text())["timestamps_raw"]) == MAX_FRAMES


def test_byte_budget_is_independent_of_frame_budget(tmp_path):
    sample = NativeAudioSample()
    sample.consume(V1Record(audio=(b"x" * MAX_BYTES, b"excess")))
    sample.close()
    report = sample.save(tmp_path)
    assert report["frames"] == 1 and report["bytes"] == MAX_BYTES and report["adts_sync"] is False
