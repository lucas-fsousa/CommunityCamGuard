"""Synthetic complete records; no camera traffic or media-validity claims."""

import struct

import pytest

from backend.app.drivers.yoosee.p2p.push_rtc_headers import parse_rtc_header_entries


def record(count, entries, *, kind=0x81):
    body = bytes([0, count]) + entries
    return struct.pack("<HHI", kind, 0xFF00, len(body)) + body


@pytest.mark.parametrize("count", [0, 1, 2, 255])
def test_entries_remain_opaque_and_ordered(count):
    entries = tuple(bytes([index]) * 20 for index in range(count))
    assert parse_rtc_header_entries(record(count, b"".join(entries))) == entries


@pytest.mark.parametrize("count,size", [(0, 20), (1, 0), (1, 19), (1, 21), (2, 20), (255, 0)])
def test_count_must_match_even_when_outer_length_is_valid(count, size):
    with pytest.raises(ValueError, match="count"):
        parse_rtc_header_entries(record(count, bytes(size)))


@pytest.mark.parametrize("kind", [0x80, 0x82, 0x83, 0xF1, 0xF2, 0x99])
def test_other_kinds_not_inferred(kind):
    with pytest.raises(ValueError):
        parse_rtc_header_entries(record(1, bytes(20), kind=kind))


@pytest.mark.parametrize("frame", [
    b"", bytes(7), bytearray(10),
    struct.pack("<HHI", 0x81, 0, 0),
    struct.pack("<HHI", 0x81, 0, 1) + b"\0",
    struct.pack("<HHI", 0x81, 0, 0xFFFFFFFF) + b"\0\0",
    record(0, b"") + b"\0", bytes(0x8400),
])
def test_malformed_records_fail_closed(frame):
    with pytest.raises(ValueError):
        parse_rtc_header_entries(frame)
