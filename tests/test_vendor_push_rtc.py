"""Cipher boundaries only: no guessed key, decryption or camera traffic."""

import struct

import pytest

from backend.app.drivers.yoosee.p2p.push_rtc import selective_rtc_cipher_span

KINDS = [(0x80, 24), (0x81, 8), (0x82, 8), (0x83, 8), (0xF1, 20), (0xF2, 20)]


def record(kind, size):
    return struct.pack("<HHI", kind, 0x1234, size - 8) + bytes(size - 8)


@pytest.mark.parametrize("kind,offset", KINDS)
@pytest.mark.parametrize("extra", [0, 1, 7, 8, 17])
def test_exact_cipher_region_without_assuming_block_rounding(kind, offset, extra):
    frame = record(kind, offset + extra)
    before = bytes(frame)
    assert selective_rtc_cipher_span(frame) == (offset, extra)
    assert frame == before


@pytest.mark.parametrize("kind,offset", KINDS)
def test_all_truncations_rejected(kind, offset):
    frame = record(kind, offset + 16)
    for size in range(len(frame)):
        with pytest.raises(ValueError):
            selective_rtc_cipher_span(frame[:size])
    for size in range(8, offset):
        with pytest.raises(ValueError, match="clear prefix"):
            selective_rtc_cipher_span(record(kind, size))


@pytest.mark.parametrize("kind", [0, 4, 8, 0x84, 0xFFFF])
def test_unknown_record_type_does_not_select_plaintext(kind):
    with pytest.raises(ValueError, match="unsupported"):
        selective_rtc_cipher_span(record(kind, 24))


def test_boundaries_and_forged_length():
    assert selective_rtc_cipher_span(record(0x80, 0x8400 - 20)) == (24, 0x8400 - 44)
    with pytest.raises(ValueError, match="size"):
        selective_rtc_cipher_span(record(0x80, 0x8400 - 19))
    for claimed in (0, 15, 17, 0xFFFFFFFF):
        frame = struct.pack("<HHI", 0x80, 0, claimed) + bytes(16)
        with pytest.raises(ValueError, match="length"):
            selective_rtc_cipher_span(frame)
    with pytest.raises(ValueError):
        selective_rtc_cipher_span(bytearray(record(0x80, 24)))
