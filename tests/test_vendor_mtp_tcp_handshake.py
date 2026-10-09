import struct

import pytest

from backend.app.drivers.yoosee.p2p.media_protocol import (
    parse_media_meter,
    verify_mtp_frame,
)
from backend.app.drivers.yoosee.p2p.mtp_tcp_handshake import build_mtp_tcp_pair_request


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
