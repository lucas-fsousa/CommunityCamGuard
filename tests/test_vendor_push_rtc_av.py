"""Synthetic payload boundaries, not real codec fixtures."""

import struct

import pytest

from backend.app.drivers.yoosee.p2p.push_rtc_av import parse_rtc_av_units


def record(flags=2, count=0, payload=b"secret", clock=100):
    return struct.pack("<HHI", 0x80, 0, 16 + len(payload)) + bytes([
        flags, 0, 0, count, 9, 0, 0, 0,
    ]) + struct.pack("<Q", clock) + payload


def part(payload, delta=0):
    return struct.pack("<HIH", len(payload), delta, 0xABCD) + payload


def test_single_raw_fields_and_hidden_payload():
    unit, = parse_rtc_av_units(record(flags=6))
    assert (unit.discriminator, unit.clock_raw, unit.flag_raw, unit.index_raw, unit.tag_raw) == (1, 100, 1, -1, 9)
    assert unit.payload == b"secret"
    assert "secret" not in repr(unit)
    assert unit.media_kind == "video"
    assert unit.pts_raw == 100
    assert unit.is_key_frame is True
    assert unit.sequence_number == 9


def test_grouped_precedence_count_nibble_and_clock_wrap():
    units = parse_rtc_av_units(record(flags=3, count=0xF2,
                                     payload=part(b"a", 1) + part(b"b", 4),
                                     clock=0xFFFFFFFFFFFFFFFD))
    assert [u.payload for u in units] == [b"a", b"b"]
    assert [u.clock_raw for u in units] == [0xFFFFFFFFFFFFFFFE, 1]
    assert all(u.discriminator == 0 and u.flag_raw == 1 for u in units)
    assert all(u.media_kind == "audio" and u.is_key_frame is None for u in units)


def test_non_key_video_and_unmapped_discriminator():
    from dataclasses import replace

    unit, = parse_rtc_av_units(record(flags=2))
    assert unit.is_key_frame is False
    unknown = replace(unit, discriminator=99)
    assert unknown.media_kind is None
    assert unknown.is_key_frame is None


@pytest.mark.parametrize("count", [1, 15])
def test_count_bound_and_zero_length_structural_units(count):
    assert len(parse_rtc_av_units(record(flags=1, count=count, payload=part(b"") * count))) == count


@pytest.mark.parametrize("data", [
    b"", bytearray(24), bytes(256 * 1024 + 1), record(flags=0),
    record(flags=1, count=0, payload=b""),
    record(flags=1, count=1, payload=bytes(7)),
    record(flags=1, count=1, payload=part(b"abc")[:-1]),
    record(flags=1, count=1, payload=part(b"abc") + b"extra"),
    record(flags=1, count=2, payload=part(b"abc")),
    record() + b"extra",
    struct.pack("<HHI", 0x82, 0, 16) + bytes(16),
])
def test_bad_records_publish_no_partial_units(data):
    with pytest.raises(ValueError):
        parse_rtc_av_units(data)


def test_maximum_reassembled_record():
    unit, = parse_rtc_av_units(record(payload=bytes(256 * 1024 - 24)))
    assert len(unit.payload) == 256 * 1024 - 24
