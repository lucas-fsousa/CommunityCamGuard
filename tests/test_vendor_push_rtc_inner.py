"""Complete synthetic streams; no recursive SDK parser or network involved."""

import struct

import pytest

from backend.app.drivers.yoosee.p2p.push_rtc_assembly import RTCFragmentAssembly
from backend.app.drivers.yoosee.p2p.push_rtc_inner import split_inner_rtc_records


def record(kind, body):
    return struct.pack("<HHI", kind, 0, len(body)) + body


RECORDS = (
    record(0x80, bytes(16) + b"opaque AV"),
    record(0x81, b"\0\1" + bytes(20)),
    record(0x82, b"opaque user data"),
    record(0x83, b"\0\0"),
)


def test_complete_mixed_stream_preserves_types_and_order():
    assert split_inner_rtc_records(b"".join(RECORDS)) == RECORDS


def test_reassembled_record_can_exceed_outer_transport_limit():
    large = record(0x80, bytes(256 * 1024 - 8))
    assert split_inner_rtc_records(large) == (large,)
    with pytest.raises(ValueError, match="size"):
        split_inner_rtc_records(large + b"x")


@pytest.mark.parametrize("kind", [0xF0, 0xF1, 0xF2, 0xF3, 0x99, 0x180])
def test_nested_and_unknown_types_rejected(kind):
    with pytest.raises(ValueError, match="type"):
        split_inner_rtc_records(RECORDS[0] + record(kind, bytes(12)))


@pytest.mark.parametrize("tail", [
    b"x", bytes(7), record(0x82, b""), record(0x80, bytes(15)),
    record(0x81, b"\0\1"), record(0x83, b"\0\0extra"),
    struct.pack("<HHI", 0x82, 0, 0xFFFFFFFF),
])
def test_valid_prefix_does_not_publish_partial_result(tail):
    with pytest.raises(ValueError):
        split_inner_rtc_records(RECORDS[0] + tail)


@pytest.mark.parametrize("data", [b"", None, bytearray(RECORDS[0])])
def test_immutable_nonempty_input_required(data):
    with pytest.raises(ValueError):
        split_inner_rtc_records(data)


def test_record_count_boundary():
    item = record(0x82, b"x")
    assert len(split_inner_rtc_records(item * 256)) == 256
    with pytest.raises(ValueError, match="count"):
        split_inner_rtc_records(item * 257)


@pytest.mark.parametrize("cut", [1, 7, 8, 9, 23, 30])
def test_fragment_assembly_then_atomic_inner_validation(cut):
    original = b"".join(RECORDS)
    assembly = RTCFragmentAssembly()
    prefix = struct.pack("<i", 7) + bytes(8)
    assert assembly.feed(record(0xF0, prefix + original[:cut])) is None
    completed = assembly.feed(record(0xF2, prefix + original[cut:]))
    assert split_inner_rtc_records(completed) == RECORDS
