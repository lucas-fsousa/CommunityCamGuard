"""Synthetic plaintext layering; never proof of relay/session authentication."""

import struct

import pytest

from backend.app.drivers.yoosee.p2p.push_framing import PushTcpFramer
from backend.app.drivers.yoosee.p2p.push_rtc_assembly import RTCFragmentAssembly
from backend.app.drivers.yoosee.p2p.push_rtc_inner import split_inner_rtc_records


def fragment(kind, payload):
    return struct.pack("<HHIi", kind, 0, 12 + len(payload), 7) + bytes(8) + payload


def outer(payload):
    # Only framing is under test: no invented checksum/authentication acceptance.
    return struct.pack("<BBHHHIQ", 3, 8, 0, len(payload), 0, 1, 2) + payload


INNER = struct.pack("<HHI", 0x82, 0, 7) + b"fixture"
WIRE = outer(fragment(0xF0, INNER[:5])) + outer(fragment(0xF2, INNER[5:]))


@pytest.mark.parametrize("cut", range(len(WIRE) + 1))
def test_every_tcp_split_preserves_complete_inner_record(cut):
    framer, assembly = PushTcpFramer(), RTCFragmentAssembly()
    records = []
    for chunk in (WIRE[:cut], WIRE[cut:]):
        for message in framer.feed(chunk):
            assembled = assembly.feed(message[20:])
            if assembled is not None:
                records.extend(split_inner_rtc_records(assembled))
    framer.finish()
    assembly.finish()
    assert records == [INNER]


def test_clean_tcp_eof_does_not_hide_incomplete_fragment_stream():
    framer, assembly = PushTcpFramer(), RTCFragmentAssembly()
    for message in framer.feed(outer(fragment(0xF0, INNER))):
        assert assembly.feed(message[20:]) is None
    framer.finish()  # Complete relay envelope is not a complete fragment stream.
    with pytest.raises(ValueError, match="truncated RTC fragment stream"):
        assembly.finish()
    assert assembly._size == 0
    assert not assembly._pending


def test_complete_fragments_do_not_hide_truncated_inner_record():
    framer, assembly = PushTcpFramer(), RTCFragmentAssembly()
    wire = outer(fragment(0xF0, INNER)) + outer(fragment(0xF2, b"tail"))
    published = []
    for message in framer.feed(wire):
        assembled = assembly.feed(message[20:])
        if assembled is not None:
            with pytest.raises(ValueError):
                published.extend(split_inner_rtc_records(assembled))
    framer.finish()
    assembly.finish()  # Fragment completeness is not inner-record validity.
    assert published == []
