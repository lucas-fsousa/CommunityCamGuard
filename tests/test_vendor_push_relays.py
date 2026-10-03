"""Synthetic descriptor parsing only; no vendor endpoint or network traffic."""

import ipaddress
import struct

import pytest

from backend.app.drivers.yoosee.p2p.push_relays import parse_push_relays


def distribution(v4=1, v6=1, tail=b""):
    token = b"not-a-real-token"
    frame = bytearray(0x88 + len(token) + 2)
    frame[:2] = b"\x7e\xe4"
    struct.pack_into("<H", frame, 0x1E, len(token))
    struct.pack_into("<I", frame, 0x28, 123)
    struct.pack_into("<Q", frame, 0x38, 789)
    frame[0x88:0x88 + len(token)] = token
    frame[-2:] = bytes((v4, v6))
    for count, address in ((v4, "192.0.2.7"), (v6, "2001:db8::7")):
        for _ in range(count):
            frame.extend(b"\0" * 8 + struct.pack("<HH", 0xA3, 4321))
            frame.extend(ipaddress.ip_address(address).packed)
    frame.extend(tail)
    struct.pack_into("<H", frame, 2, len(frame))
    return bytes(frame)


def parse(frame, **kwargs):
    return parse_push_relays(frame, **(dict(expected_device_id=789, expected_link_id=123) | kwargs))


def test_both_families_and_little_endian_port():
    result = parse(distribution())
    assert result is not None
    assert [(r.family, r.address, r.port) for r in result] == [
        (4, "192.0.2.7", 4321), (6, "2001:db8::7", 4321),
    ]
    assert result[0].advertises_tcp and result[0].advertises_udp
    assert result[0].cluster_id == 10
    # SDK selection reads the IPv4 descriptor; do not transfer those semantics
    # to the IPv6 field just because its byte offset happens to match.
    assert result[1].advertises_tcp is None
    assert result[1].advertises_udp is None
    assert result[1].cluster_id is None
    for relay in result:
        assert relay.address not in repr(relay)
        assert "4321" not in repr(relay)


@pytest.mark.parametrize("flags", [0, 1, 2, 3, 0xFFF0, 0xFFFF])
def test_flags_are_advertisements_not_certification(flags):
    frame = bytearray(distribution(1, 0))
    struct.pack_into("<HH", frame, len(frame) - 8, flags, 0)
    result = parse(bytes(frame))
    assert result is not None
    relay, = result
    assert relay.flags == flags
    assert relay.port == 0
    assert relay.advertises_tcp == bool(flags & 1)
    assert relay.advertises_udp == bool(flags & 2)
    assert relay.cluster_id == (flags >> 4) & 15


@pytest.mark.parametrize("v4,v6", [(0, 0), (8, 0), (0, 8), (8, 8)])
def test_local_count_boundaries(v4, v6):
    result = parse(distribution(v4, v6))
    assert result is not None and len(result) == v4 + v6


@pytest.mark.parametrize("v4,v6", [(9, 0), (0, 9), (9, 9)])
def test_excess_descriptors_rejected(v4, v6):
    assert parse(distribution(v4, v6)) is None


def test_every_truncated_prefix_and_unknown_extension_rejected():
    frame = distribution()
    for length in range(len(frame)):
        assert parse(frame[:length]) is None
    assert parse(distribution(tail=b"x")) is None


@pytest.mark.parametrize("kwargs", [dict(expected_device_id=790), dict(expected_link_id=124)])
def test_other_session_rejected(kwargs):
    assert parse(distribution(), **kwargs) is None


@pytest.mark.parametrize("offset,value", [(0, 0), (1, 0), (0x16, 0x10), (0x1F, 0xFF)])
def test_bad_envelope_rejected(offset, value):
    frame = bytearray(distribution())
    frame[offset] = value
    assert parse(bytes(frame)) is None
