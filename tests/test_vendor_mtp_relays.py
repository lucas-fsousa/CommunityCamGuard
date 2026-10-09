import ipaddress
import struct

import pytest

from backend.app.drivers.yoosee.p2p.mtp_relays import parse_mtp_relays


def packet(v4=1, v6=1):
    frame = bytearray(0x7A + v4 * 16 + v6 * 28)
    frame[:2] = b"\x7e\xa3"
    struct.pack_into("<H", frame, 2, len(frame))
    struct.pack_into("<Q", frame, 4, 17)
    struct.pack_into("<I", frame, 0x14, 2 << 16)
    struct.pack_into("<I", frame, 0x1C, 31)
    frame[0x78:0x7A] = bytes((v4, v6))
    offset = 0x7A
    for count, stride, address in ((v4, 16, "192.0.2.1"), (v6, 28, "2001:db8::1")):
        for _ in range(count):
            struct.pack_into("<H", frame, offset + 8, 4)
            struct.pack_into(">H", frame, offset + 10, 19800)
            raw = ipaddress.ip_address(address).packed
            frame[offset + 12:offset + 12 + len(raw)] = raw
            offset += stride
    return bytes(frame)


def parse(frame):
    return parse_mtp_relays(frame, expected_session_id=17, expected_link_id=31)


def test_tables_preserve_independent_family_indices_and_private_repr():
    result = parse(packet(2, 1))
    assert result is not None
    assert [(d.family, d.index, d.flags, d.port) for d in result] == [
        (4, 0, 4, 19800), (4, 1, 4, 19800), (6, 0, 4, 19800),
    ]
    assert result[0].address == "192.0.2.1"
    assert result[2].address == "2001:db8::1"
    assert "192.0.2.1" not in repr(result) and "19800" not in repr(result)


def test_empty_and_maximum_tables():
    assert parse(packet(0, 0)) == ()
    assert len(parse(packet(32, 16))) == 48
    assert parse(packet(33, 0)) is None
    assert parse(packet(0, 17)) is None


@pytest.mark.parametrize("length", range(len(packet())))
def test_every_truncated_prefix_is_rejected(length):
    assert parse(packet()[:length]) is None


@pytest.mark.parametrize("offset,value", [
    (0, 0x70), (1, 0xE4), (2, 0), (4, 18), (0x1C, 32),
    (0x14, 1), (0x16, 1), (0x16, 0x12), (0x78, 2), (0x79, 2),
])
def test_invalid_envelopes_are_rejected(offset, value):
    frame = bytearray(packet())
    frame[offset] = value
    assert parse(bytes(frame)) is None


def test_trailing_extension_rejected_even_with_correct_declared_length():
    frame = bytearray(packet() + b"\0")
    struct.pack_into("<H", frame, 2, len(frame))
    assert parse(bytes(frame)) is None
