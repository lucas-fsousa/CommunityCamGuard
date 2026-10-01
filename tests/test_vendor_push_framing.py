"""Synthetic TCP fragmentation/EOF/admission tests; no sockets."""

import struct

import pytest

from backend.app.drivers.yoosee.p2p.push_framing import (
    MAX_FEED_BYTES,
    MAX_FRAME_BYTES,
    MAX_FRAMES_PER_FEED,
    PushFrameError,
    PushTcpFramer,
)


def packet(body=b"synthetic", kind=7):
    header = bytearray(20)
    header[:2] = bytes((3, kind))
    struct.pack_into("<H", header, 4, len(body))
    return bytes(header) + body


def test_every_two_part_split():
    wire = packet()
    for split in range(len(wire) + 1):
        parser = PushTcpFramer()
        assert parser.feed(wire[:split]) + parser.feed(wire[split:]) == [wire]
        assert parser.buffered_bytes == 0
        parser.finish()


def test_single_byte_reads_and_coalesced_frames():
    wire = packet() + packet(b"next", kind=13)
    parser = PushTcpFramer()
    result = []
    for byte in wire:
        result.extend(parser.feed(bytes((byte,))))
    assert result == [packet(), packet(b"next", kind=13)]
    assert PushTcpFramer().feed(wire) == result


def test_retains_only_incomplete_suffix():
    parser = PushTcpFramer()
    assert parser.feed(packet() + packet()[:15]) == [packet()]
    assert parser.buffered_bytes == 15
    assert parser.feed(packet()[15:]) == [packet()]


def test_largest_frame_split_across_bounded_reads():
    wire = packet(b"x" * (MAX_FRAME_BYTES - 20))
    parser = PushTcpFramer()
    assert parser.feed(wire[:-1]) == []
    assert parser.feed(wire[-1:]) == [wire]
    parser.finish()


@pytest.mark.parametrize("data", [packet(b""), b"\x04" + packet()[1:],
                                  packet(b"x" * (MAX_FRAME_BYTES - 19))[:20]])
def test_invalid_header_closes_decoder(data):
    parser = PushTcpFramer()
    with pytest.raises(PushFrameError):
        parser.feed(data)
    assert parser.buffered_bytes == 0
    with pytest.raises(PushFrameError, match="closed"):
        parser.feed(packet())


def test_every_incomplete_eof_is_rejected():
    wire = packet()
    for split in range(1, len(wire)):
        parser = PushTcpFramer()
        parser.feed(wire[:split])
        with pytest.raises(PushFrameError, match="truncated"):
            parser.finish()
        assert parser.buffered_bytes == 0


def test_clean_eof_prevents_reuse():
    parser = PushTcpFramer()
    parser.finish()
    with pytest.raises(PushFrameError, match="closed"):
        parser.feed(b"")


@pytest.mark.parametrize("data", [b"x" * (MAX_FEED_BYTES + 1), bytearray(b"x"), "x"])
def test_admission_limits(data):
    with pytest.raises(PushFrameError):
        PushTcpFramer().feed(data)


def test_frame_count_limit():
    parser = PushTcpFramer()
    assert len(parser.feed(packet(b"x") * MAX_FRAMES_PER_FEED)) == MAX_FRAMES_PER_FEED
    with pytest.raises(PushFrameError, match="batch"):
        parser.feed(packet(b"x") * (MAX_FRAMES_PER_FEED + 1))
    assert parser.buffered_bytes == 0


def test_valid_prefix_with_invalid_suffix_is_not_returned():
    with pytest.raises(PushFrameError):
        PushTcpFramer().feed(packet() + packet(b""))
