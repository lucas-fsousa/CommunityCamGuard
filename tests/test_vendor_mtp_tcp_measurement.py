import struct

import pytest

from backend.app.drivers.yoosee.p2p.media_protocol import build_mtp_frame, verify_mtp_frame
from backend.app.drivers.yoosee.p2p.mtp_tcp_measurement import (
    build_mtp_tcp_measurement,
    matches_mtp_tcp_measurement_ack,
)


def fields():
    return dict(relay_link_id=0x123456, source_id=17, destination_id=23,
                call_id=0x11223344, sequence=9, timestamp_ms=(1 << 40) + 7,
                session_role=1)


@pytest.mark.parametrize("channel,offset,prefix", [(0x85, 6, 0x80), (0x86, 14, 0xE0)])
def test_sdk_periodic_measurement_exact_layout(channel, offset, prefix):
    wire = build_mtp_tcp_measurement(**fields(), channel_type=channel)
    assert len(wire) == offset + 72
    assert wire[:2] == bytes((0xC0, prefix))
    assert verify_mtp_frame(wire)
    if offset == 14:
        assert wire[6:14] == struct.pack("<Q", 23)
    # Independently assign each SDK field, including the two distinct lengths.
    expected = bytearray(72)
    expected[1] = 1
    for fmt, at, value in [("H", 2, 68), ("I", 4, 0x123456), ("I", 8, 8),
                           ("Q", 12, 17), ("Q", 20, 23), ("I", 28, 9),
                           ("Q", 32, (1 << 40) + 7), ("I", 52, 72),
                           ("I", 68, 0x11223344)]:
        struct.pack_into("<" + fmt, expected, at, value)
    expected[64:66] = b"\x02\x01"
    assert wire[offset:] == expected


@pytest.mark.parametrize("field,width", [
    ("relay_link_id", 32), ("source_id", 64), ("destination_id", 64),
    ("call_id", 32), ("sequence", 32), ("timestamp_ms", 64), ("session_role", 8),
])
@pytest.mark.parametrize("invalid", ["negative", "overflow", "bool", "float"])
def test_unsigned_fields_never_silently_truncate(field, width, invalid):
    args = fields()
    args[field] = {"negative": -1, "overflow": 1 << width, "bool": True, "float": 1.0}[invalid]
    with pytest.raises(ValueError):
        build_mtp_tcp_measurement(**args)


@pytest.mark.parametrize("channel", [True, 0x86 + 0.0, 0, 3, 0x87, 0x88, -1, 0x186])
def test_unsupported_transport_rejected(channel):
    with pytest.raises(ValueError):
        build_mtp_tcp_measurement(**fields(), channel_type=channel)


@pytest.mark.parametrize("sequence", [7, 8, 0xFFFFFFFF])
def test_tcp_never_gets_udp_mtu_probe_padding(sequence):
    args = fields()
    args["sequence"] = sequence
    assert len(build_mtp_tcp_measurement(**args)) == 86


def ack_fixture():
    body = bytearray(72)
    body[:4] = b"\x00\x02\x44\x00"
    struct.pack_into("<I", body, 4, 7)
    struct.pack_into("<QQIQ", body, 12, 23, 17, 9, 1 << 40)
    struct.pack_into("<I", body, 52, 72)
    body[64] = 2
    expected = dict(expected_link_id=7, expected_source_id=23, expected_destination_id=17,
                    expected_sequence=9, expected_timestamp_ms=1 << 40)
    return body, expected


def test_ack_requires_all_correlation_fields_and_int_widths():
    body, expected = ack_fixture()
    wire = build_mtp_frame(0xD0, b"opaque!!" + body)
    assert matches_mtp_tcp_measurement_ack(wire, **expected)
    for field in expected:
        for value in (expected[field] + 1, True, -1, 1 << 65, float(expected[field])):
            altered = {**expected, field: value}
            assert not matches_mtp_tcp_measurement_ack(wire, **altered)


@pytest.mark.parametrize("offset,value", [(0, 1), (1, 1), (1, 3), (2, 72), (8, 8), (52, 68)])
def test_checksum_valid_wrong_ack_layout_is_rejected(offset, value):
    body, expected = ack_fixture()
    body[offset] = value
    assert not matches_mtp_tcp_measurement_ack(
        build_mtp_frame(0xD0, b"opaque!!" + body), **expected,
    )


def test_ack_truncation_corruption_other_envelopes_and_trailing_bytes():
    body, expected = ack_fixture()
    wire = build_mtp_frame(0xD0, b"opaque!!" + body)
    for length in range(len(wire)):
        assert not matches_mtp_tcp_measurement_ack(wire[:length], **expected)
    # MTP checksums only the first 24 payload bytes, not the complete record.
    for offset in range(30):
        broken = bytearray(wire)
        broken[offset] ^= 1
        assert not matches_mtp_tcp_measurement_ack(bytes(broken), **expected)
    assert not matches_mtp_tcp_measurement_ack(wire + b"\x00", **expected)
    for prefix in (0x80, 0x90, 0xE0):
        assert not matches_mtp_tcp_measurement_ack(
            build_mtp_frame(prefix, b"opaque!!" + body), **expected,
        )


def test_uninterpreted_tail_is_not_claimed_to_be_checksum_protected():
    body, expected = ack_fixture()
    wire = bytearray(build_mtp_frame(0xD0, b"opaque!!" + body))
    wire[-1] ^= 1
    assert verify_mtp_frame(bytes(wire))
    assert matches_mtp_tcp_measurement_ack(bytes(wire), **expected)
