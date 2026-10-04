"""Synthetic descriptors with deliberately asymmetric values to pin offsets."""

import struct

import pytest

from backend.app.drivers.yoosee.p2p.push_rtc_formats import (
    RTCAudioFormat,
    RTCVideoFormat,
    parse_rtc_format,
)
from backend.app.drivers.yoosee.p2p.push_rtc_headers import parse_rtc_header_entries


def video(rate=29.5, codec=5, index=3):
    return bytes([0xAA, 1, index, 0xBB]) + struct.pack("<HHf", 1920, 1080, rate) + bytes([codec]) + bytes(7)


def test_video_fields_and_no_rate_correction():
    item = parse_rtc_format(video(2.5))
    assert isinstance(item, RTCVideoFormat)
    assert (item.stream_index, item.width, item.height, item.frame_rate) == (2, 1920, 1080, 2.5)
    assert item.codec_id == 5 and item.avcodec_id == 173
    assert item.raw == video(2.5)


def test_audio_fields_and_distinct_index_namespace():
    raw = bytes([0xAA, 2, 3, 0xBB]) + struct.pack("<IH", 16000, 320) + bytes([2, 16, 4, 7]) + bytes(6)
    item = parse_rtc_format(raw)
    assert isinstance(item, RTCAudioFormat)
    assert (item.stream_index, item.codec_id, item.codec_option) == (2, 4, 7)
    assert (item.channels, item.bit_width, item.sample_rate, item.frame_size) == (2, 16, 16000, 320)
    assert item.avcodec_id == 86018


@pytest.mark.parametrize("index,expected", [(0, 0), (1, 0), (255, 254)])
def test_consumer_index_mapping(index, expected):
    assert parse_rtc_format(video(index=index)).stream_index == expected


def test_unknown_codec_and_zero_values_preserved():
    assert parse_rtc_format(video(codec=255)).avcodec_id is None
    assert parse_rtc_format(bytes([0, 1]) + bytes(18)).frame_rate == 0
    assert parse_rtc_format(bytes([0, 2]) + bytes(18)).sample_rate == 0


@pytest.mark.parametrize("raw", [b"", bytes(19), bytes(21), bytearray(20), bytes(20),
                                  video(float("nan")), video(float("inf")), video(-float("inf"))])
def test_invalid_descriptor(raw):
    with pytest.raises(ValueError):
        parse_rtc_format(raw)


@pytest.mark.parametrize("kind", [0x81, 0x83])
def test_header_entry_integration(kind):
    body = b"\0\1" + video()
    entries = parse_rtc_header_entries(struct.pack("<HHI", kind, 0, len(body)) + body)
    assert parse_rtc_format(entries[0]).raw == video()
