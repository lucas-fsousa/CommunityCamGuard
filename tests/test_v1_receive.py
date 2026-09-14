import struct

import pytest

from backend.app.drivers.yoosee.p2p.stream_protocol import build_v1_audio_packet
from backend.app.drivers.yoosee.p2p.v1_receive import MAX_CHUNK, MAX_RECORD, V1Receiver
from scripts.captured_frames import CapturedFrames
from tests.test_captured_media import header


def av(audio=(b"abc", b"defg"), video=b"video", audio_ts=123, video_ts=456):
    raw = bytearray(build_v1_audio_packet(audio, audio_ts, record_marker=8))
    struct.pack_into("<I", raw, 8, len(video))
    struct.pack_into("<Q", raw, 12, video_ts)
    return bytes(raw) + video


@pytest.mark.parametrize("chunk", [1, 2, 7, 27, 28, 29, 32, 65535])
def test_arbitrary_chunk_boundaries_preserve_frames_and_timestamps(chunk):
    receiver = V1Receiver()
    source = header() + av()
    records = []
    for offset in range(0, len(source), chunk):
        records.extend(receiver.feed(source[offset:offset + chunk]))
    assert len(records) == 2
    assert records[0].encoding.video_width == 1920
    assert records[1].audio == (b"abc", b"defg")
    assert records[1].video == b"video"
    assert (records[1].audio_timestamp, records[1].video_timestamp) == (123, 456)
    assert receiver.buffered_bytes == 0


def test_partial_record_never_emitted_or_resynchronized_on_payload_magic():
    receiver = V1Receiver()
    source = av(video=header())
    assert not receiver.feed(source[:-1])
    result, = receiver.feed(source[-1:])
    assert result.video == header()
    assert receiver.buffered_bytes == 0


def test_header_change_and_empty_record():
    receiver = V1Receiver()
    empty = bytearray(28)
    empty[:4] = bytes.fromhex("ffffff88")
    records = receiver.feed(header() + bytes(empty) + header())
    assert len(records) == 3
    assert records[1].encoding is None and not records[1].audio and not records[1].video


@pytest.mark.parametrize("fault", ["magic", "marker", "count", "length", "zero_audio", "chunk"])
def test_bad_lengths_and_unknown_records_close_without_unbounded_allocation(fault):
    receiver = V1Receiver()
    source = bytearray(av())
    if fault == "magic":
        source[0] = 0
    elif fault == "marker":
        struct.pack_into("<H", source, 4, 0x300)
    elif fault == "count":
        struct.pack_into("<H", source, 6, 65535)
    elif fault == "length":
        struct.pack_into("<I", source, 8, MAX_RECORD)
    elif fault == "zero_audio":
        struct.pack_into("<H", source, 28, 0)
    else:
        source = bytearray(MAX_CHUNK + 1)
    with pytest.raises(ValueError):
        receiver.feed(bytes(source))
    assert receiver.closed and receiver.buffered_bytes == 0
    with pytest.raises(ValueError, match="closed"):
        receiver.feed(b"")


def test_stats_report_relative_deltas_not_raw_timestamps_and_preserve_tail():
    stats = CapturedFrames()
    stats.consume(header() + av(audio_ts=1000000, video_ts=1000000))
    stats.consume(av(audio_ts=1064000, video_ts=1066000))
    stats.consume(av(audio_ts=1063000, video_ts=1065000))
    stats.consume(av()[:15])
    result = stats.finish()
    assert result["audio_frames"] == 6 and result["video_frames"] == 3
    assert result["audio_delta_max"] == 64000 and result["video_delta_max"] == 66000
    assert result["audio_regressions"] == result["video_regressions"] == 1
    assert result["incomplete_tail_bytes"] == 15
    assert stats.receiver.closed and stats.receiver.buffered_bytes == 0
