import struct

import pytest

from backend.app.drivers.yoosee.p2p.media_protocol import (
    build_mtp_frame,
    parse_media_meter,
    verify_mtp_frame,
)
from backend.app.drivers.yoosee.p2p.mtp_tcp_handshake import (
    build_mtp_tcp_pair_request,
    parse_mtp_tcp_meter,
)


def test_sdk_tcp_pair_request_has_distinct_subtype_and_zero_reserved_fields():
    wire = build_mtp_tcp_pair_request(
        relay_link_id=0x11223344, source_id=17, destination_id=23, timestamp_ms=1 << 40,
    )
    assert len(wire) == 74
    assert wire[:4] == b"\xc0\x80\x02\x09"
    assert verify_mtp_frame(wire)
    expected = bytearray(68)
    expected[1] = 1
    struct.pack_into("<H", expected, 2, 68)
    struct.pack_into("<I", expected, 4, 0x11223344)
    struct.pack_into("<Q", expected, 12, 17)
    struct.pack_into("<Q", expected, 20, 23)
    struct.pack_into("<Q", expected, 32, 1 << 40)
    assert wire[6:] == expected
    assert parse_media_meter(wire) is None


@pytest.mark.parametrize("field,width", [
    ("relay_link_id", 32), ("source_id", 64), ("destination_id", 64), ("timestamp_ms", 64),
])
@pytest.mark.parametrize("invalid", ["negative", "overflow", "bool", "float"])
def test_no_implicit_truncation_or_numeric_coercion(field, width, invalid):
    kwargs = dict(relay_link_id=1, source_id=2, destination_id=3, timestamp_ms=4)
    kwargs[field] = {"negative": -1, "overflow": 1 << width, "bool": True, "float": 1.0}[invalid]
    with pytest.raises(ValueError):
        build_mtp_tcp_pair_request(**kwargs)


def test_extended_request_is_correlated_without_becoming_an_ack():
    body = build_mtp_tcp_pair_request(
        relay_link_id=7, source_id=23, destination_id=17, timestamp_ms=1 << 40,
    )[6:]
    wire = build_mtp_frame(0xD0, b"opaque!!" + body)
    result = parse_mtp_tcp_meter(
        wire, expected_link_id=7, expected_source_id=23, expected_destination_id=17,
    )
    assert result is not None and result.kind == 1 and result.timestamp_ms == 1 << 40
    assert parse_media_meter(wire) is None
    for field in ("expected_link_id", "expected_source_id", "expected_destination_id"):
        expected = dict(expected_link_id=7, expected_source_id=23, expected_destination_id=17)
        expected[field] += 1
        assert parse_mtp_tcp_meter(wire, **expected) is None
    for length in range(len(wire)):
        assert parse_mtp_tcp_meter(
            wire[:length], expected_link_id=7, expected_source_id=23, expected_destination_id=17,
        ) is None
    for offset in (0, 1, 2, 4, 14, 15, 16, 18):
        corrupt = bytearray(wire)
        corrupt[offset] ^= 0xFF
        assert parse_mtp_tcp_meter(
            bytes(corrupt), expected_link_id=7, expected_source_id=23, expected_destination_id=17,
        ) is None
