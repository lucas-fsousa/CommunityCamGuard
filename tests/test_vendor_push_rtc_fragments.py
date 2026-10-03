"""Synthetic fragment records; no live media or proprietary code execution."""

import struct

import pytest

from backend.app.drivers.yoosee.p2p.push_rtc_assembly import RTCFragmentAssembly
from backend.app.drivers.yoosee.p2p.push_rtc_fragments import parse_rtc_fragment


def frame(kind, identity=7, payload=b"", metadata=b"12345678"):
    return struct.pack("<HHIi", kind, 0, 12 + len(payload), identity) + metadata + payload


@pytest.mark.parametrize("kind", [0xF0, 0xF1, 0xF2, 0xF3])
@pytest.mark.parametrize("identity", [0, -1, -(2**31), 2**31 - 1])
def test_envelope(kind, identity):
    item = parse_rtc_fragment(frame(kind, identity, b"secret"))
    assert (item.kind, item.fragment_id, item.metadata, item.payload) == (
        kind, identity, b"12345678", b"secret",
    )
    assert item.error_code == (0x34333231 if kind == 0xF3 else None)
    assert "secret" not in repr(item)


@pytest.mark.parametrize("data", [
    b"", bytes(19), bytearray(20), bytes(0x8400), frame(0x81),
    frame(0xF0) + b"x", struct.pack("<HHI", 0xF0, 0, 0xFFFFFFFF) + bytes(12),
])
def test_invalid_envelopes(data):
    with pytest.raises(ValueError):
        parse_rtc_fragment(data)


def test_interleaved_ids_and_retirement():
    assembly = RTCFragmentAssembly()
    assert assembly.feed(frame(0xF0, 1, b"a")) is None
    assert assembly.feed(frame(0xF0, 2, b"x")) is None
    assert assembly.feed(frame(0xF1, 1, b"b")) is None
    assert assembly.feed(frame(0xF2, 2, b"y")) == b"xy"
    assert assembly.feed(frame(0xF2, 1, b"c")) == b"abc"
    assert assembly._size == 0
    assert not assembly._pending
    assembly.feed(frame(0xF0, 1))
    assert assembly.feed(frame(0xF2, 1)) == b""


def test_error_discards_only_its_assembly():
    assembly = RTCFragmentAssembly()
    assembly.feed(frame(0xF0, 1, b"discard"))
    assembly.feed(frame(0xF0, 2, b"keep"))
    assert assembly.feed(frame(0xF3, 1, b"ignored")) is None
    assert assembly.feed(frame(0xF2, 2)) == b"keep"
    assert assembly._size == 0


@pytest.mark.parametrize("bad", [frame(0xF0), frame(0xF1, 9), frame(0xF2, 9), frame(0xF3, 9), b"bad"])
def test_bad_transition_clears_and_permanently_closes(bad):
    assembly = RTCFragmentAssembly()
    assembly.feed(frame(0xF0, payload=b"pending"))
    with pytest.raises(ValueError):
        assembly.feed(bad)
    assert not assembly._pending
    assert assembly._size == 0
    with pytest.raises(ValueError, match="closed"):
        assembly.feed(frame(0xF0))


def test_count_limit_even_for_empty_fragments():
    assembly = RTCFragmentAssembly()
    assembly.feed(frame(0xF0))
    for _ in range(255):
        assembly.feed(frame(0xF1))
    with pytest.raises(ValueError, match="limit"):
        assembly.feed(frame(0xF2))
    assert not assembly._pending


def test_aggregate_byte_limit_and_pending_id_limit():
    assembly = RTCFragmentAssembly()
    for identity in range(4):
        assembly.feed(frame(0xF0, identity, bytes(32768)))
        assembly.feed(frame(0xF1, identity, bytes(32768)))
    assert assembly._size == 256 * 1024
    with pytest.raises(ValueError, match="limit"):
        assembly.feed(frame(0xF2, 0, b"x"))
    assert assembly._size == 0
    assembly = RTCFragmentAssembly()
    for identity in range(4):
        assembly.feed(frame(0xF0, identity))
    with pytest.raises(ValueError, match="conflicts"):
        assembly.feed(frame(0xF0, 4))


def test_owner_close_and_independent_sources():
    old, new = RTCFragmentAssembly(), RTCFragmentAssembly()
    old.feed(frame(0xF0, payload=b"old"))
    new.feed(frame(0xF0, payload=b"new"))
    old.close()
    old.close()
    assert not old._pending
    with pytest.raises(ValueError, match="closed"):
        old.feed(frame(0xF2))
    assert new.feed(frame(0xF2)) == b"new"
