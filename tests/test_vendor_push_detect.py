"""Synthetic, independent golden vectors for the outbound keepalive only."""

import struct

import pytest

from backend.app.drivers.yoosee.p2p.push_detect import build_push_detect


def test_known_layout_and_manually_calculated_checksum():
    packet = build_push_detect(push_id=1, access_id=2, sequence=0x00030002, tick_ms=0x04050607)
    # First four body words: 0, 0, 2, 3. Rotations yield 0, 0, 8, 24;
    # XOR with body length 24 gives checksum 8. Tick is NOT covered.
    assert packet == bytes.fromhex(
        "0304000018000800 01000000 0200000000000000"
        "00000000 02000300 0706050400000000 0000000000000000"
    )


def test_zero_and_maximum_fields_without_implicit_wrapping():
    assert len(build_push_detect(push_id=0, access_id=0, sequence=0, tick_ms=0)) == 44
    packet = build_push_detect(push_id=2**32 - 1, access_id=2**64 - 1,
                               sequence=2**32 - 1, tick_ms=2**32 - 1)
    assert struct.unpack_from("<IQ", packet, 8) == (2**32 - 1, 2**64 - 1)
    assert struct.unpack_from("<IQ", packet, 24) == (2**32 - 1, 2**32 - 1)
    assert packet[20:24] == bytes(4)
    assert packet[36:] == bytes(8)


@pytest.mark.parametrize("field,bits", [("push_id", 32), ("access_id", 64),
                                        ("sequence", 32), ("tick_ms", 32)])
@pytest.mark.parametrize("invalid", ["negative", "overflow", True, 1.0, "1", None])
def test_rejects_invalid_fields(field, bits, invalid):
    values = dict(push_id=1, access_id=2, sequence=3, tick_ms=4)
    values[field] = -1 if invalid == "negative" else 2**bits if invalid == "overflow" else invalid
    with pytest.raises(ValueError, match="out of range"):
        build_push_detect(**values)


def test_deterministic_and_does_not_advance_sequence_or_read_clock():
    values = dict(push_id=1, access_id=2, sequence=3, tick_ms=4)
    assert build_push_detect(**values) == build_push_detect(**values)
    assert values == dict(push_id=1, access_id=2, sequence=3, tick_ms=4)
