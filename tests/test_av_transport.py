"""Socket-free transport packet bounds, tables, clocks and continuity."""

import pytest

from backend.app.media.av_transport import AUDIO_PID, VIDEO_PID, AvTransport, crc32, pts_field


def test_tables_have_valid_mpeg_crc_and_packet_sizes():
    mux = AvTransport(1000000)
    data = mux.video(b"\0\0\0\1\x26\1\x80" + b"x" * 400, 1000000)
    assert len(data) % 188 == 0
    packets = [data[i:i+188] for i in range(0, len(data), 188)]
    assert all(p[0] == 0x47 for p in packets)
    assert [((p[1] & 31) << 8) | p[2] for p in packets[:3]] == [0, 0x100, VIDEO_PID]
    for packet in packets[:2]:
        start = 5 + packet[4] + 1  # adaptation length, then PSI pointer
        table = packet[start:]
        size = 3 + (((table[1] & 15) << 8) | table[2])
        assert crc32(table[:size]) == b"\0" * 4


def test_pts_clock_is_relative_microseconds_not_nominal_fps_or_unix_epoch():
    assert pts_field(0) == bytes.fromhex("2100010001")
    mux = AvTransport(900000000)
    first = mux.video(b"x" * 100, 900000000)
    second = mux.audio(b"aac", 900064000)
    assert pts_field(5760) in second  # 64ms *90kHz
    assert pts_field(0) in first
    assert ((second[1] & 31) << 8) | second[2] == AUDIO_PID


@pytest.mark.parametrize("payload,stamp", [(b"", 1), (b"x", -1), (b"x", True), (b"x" * 65528, 1)])
def test_audio_rejects_invalid_or_unbounded_input(payload, stamp):
    with pytest.raises(ValueError):
        AvTransport(0).audio(payload, stamp)
