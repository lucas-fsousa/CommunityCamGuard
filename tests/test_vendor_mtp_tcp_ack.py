import struct

import pytest

from backend.app.drivers.yoosee.p2p.media_protocol import build_mtp_frame, verify_mtp_frame
from backend.app.drivers.yoosee.p2p.mtp_tcp_ack import (
    build_mtp_tcp_meter_ack,
    build_mtp_tcp_plain_meter_ack,
)


def request(**changes):
    body = bytearray(68)
    body[1] = changes.get("kind", 1)
    struct.pack_into("<H", body, 2, 68)
    struct.pack_into("<I", body, 4, 7)
    struct.pack_into("<I", body, 8, changes.get("flags", 0))
    struct.pack_into("<Q", body, 12, 23)
    struct.pack_into("<Q", body, 20, 17)
    struct.pack_into("<I", body, 28, 99)
    struct.pack_into("<Q", body, 32, (1 << 40) + 9)
    struct.pack_into("<I", body, 48, 4)
    struct.pack_into("<I", body, 52, changes.get("length", 68))
    return build_mtp_frame(0xD0, b"opaque!!" + body)


def ack(wire, **changes):
    expected = dict(expected_link_id=7, expected_source_id=23, expected_destination_id=17)
    expected.update(changes)
    return build_mtp_tcp_meter_ack(wire, **expected)


@pytest.mark.parametrize("length", [0, 1, 67, 68])
def test_exact_sdk_extended_ack_layout(length):
    original = request(length=length)
    wire = ack(original)
    assert len(wire) == 82 and wire[:4] == b"\xc0\xe0\x02\x0a"
    assert verify_mtp_frame(wire)
    assert wire[6:14] == struct.pack("<Q", 23)
    expected = bytearray(68)
    expected[1] = 2
    struct.pack_into("<H", expected, 2, 68)
    struct.pack_into("<I", expected, 4, 7)
    struct.pack_into("<Q", expected, 12, 17)
    struct.pack_into("<Q", expected, 20, 23)
    struct.pack_into("<I", expected, 28, 99)
    struct.pack_into("<Q", expected, 32, (1 << 40) + 9)
    struct.pack_into("<I", expected, 48, 4)
    struct.pack_into("<I", expected, 52, 68)
    expected[64] = 2
    assert wire[14:] == expected
    assert original == request(length=length)


@pytest.mark.parametrize("changes", [dict(kind=2), dict(kind=3), dict(flags=0x40), dict(length=69)])
def test_unsupported_bodies_are_not_acknowledged(changes):
    with pytest.raises(ValueError):
        ack(request(**changes))


def test_wrong_route_or_damaged_frame_is_not_acknowledged():
    for field in ("expected_link_id", "expected_source_id", "expected_destination_id"):
        with pytest.raises(ValueError):
            ack(request(), **{field: 555})
    for length in range(82):
        with pytest.raises(ValueError):
            ack(request()[:length])
    corrupt = bytearray(request())
    corrupt[4] ^= 1
    with pytest.raises(ValueError):
        ack(bytes(corrupt))


@pytest.mark.parametrize("channel,subtype", [(0, 0x80), (1, 0x90), (2, 0x90), (4, 0x80)])
def test_plain_tcp_reply_uses_sdk_channel_branch_and_full_timestamp(channel, subtype):
    body = bytearray(request()[14:])
    struct.pack_into("<I", body, 48, channel)
    incoming = build_mtp_frame(0x90, bytes(body))
    result = build_mtp_tcp_plain_meter_ack(
        incoming, expected_link_id=7, expected_source_id=23, expected_destination_id=17,
    )
    assert len(result) == 74 and result[:2] == bytes((0xC0, subtype))
    assert verify_mtp_frame(result)
    assert struct.unpack_from("<Q", result, 38)[0] == (1 << 40) + 9
    assert result[7] == 2
    with pytest.raises(ValueError):
        build_mtp_tcp_plain_meter_ack(
            incoming, expected_link_id=8, expected_source_id=23, expected_destination_id=17,
        )
