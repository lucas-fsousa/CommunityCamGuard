from __future__ import annotations

import pytest

from backend.app.drivers.yoosee.p2p.quicklz import decompress_level2

# Synthetic blocks generated offline with the exact APK profile (QuickLZ 1.5, level 2,
# stream buffer 1024, memory-safe). The GPL laboratory oracle is not a runtime/test dependency.
VECTORS = (
    ("79112808000080414141607d2141414141", b"A" * 40),
    ("7912281000008041424142401d2041424142", b"AB" * 20),
    ("79113c08000080414243403f3543414243", b"ABC" * 20),
    (
        "79332800000080000102030405060708090a0b0c0d0e0f101112131415161718191a1b1c1d1e"
        "000000801f2021222324252627",
        bytes(range(40)),
    ),
    ("791a78001000807b22726573756c74223a317d601f68223a317d", b'{"result":1}' * 10),
    (
        "791e4000000180000102030405060708090a0b0c0d0e0f00022c0c0d0e0f",
        bytes(range(16)) * 4,
    ),
    ("7b1a0000002c010000180000800000000000ff00002600000000", bytes(300)),
)


@pytest.mark.parametrize(("encoded", "plain"), VECTORS)
def test_level2_decoder_matches_apk_profile_vectors(encoded, plain):
    assert decompress_level2(bytes.fromhex(encoded), len(plain)) == plain


def test_level2_decoder_handles_uncompressed_short_and_long_blocks():
    short = bytes((0x78, 7, 4)) + b"data"
    long_payload = bytes(range(256))
    long = (
        bytes((0x7A,))
        + (len(long_payload) + 9).to_bytes(4, "little")
        + len(long_payload).to_bytes(4, "little")
        + long_payload
    )

    assert decompress_level2(short, 4) == b"data"
    assert decompress_level2(long, len(long_payload)) == long_payload


def test_level2_decoder_accepts_only_the_encoders_minimum_zero_padding():
    padded = bytes.fromhex("790c01000000804100000000")

    assert decompress_level2(padded, 1) == b"A"
    with pytest.raises(ValueError, match="trailing"):
        decompress_level2(padded[:-1] + b"X", 1)


@pytest.mark.parametrize(
    "encoded",
    (
        b"",
        bytes.fromhex("79112808000080414141607d21414141"),
        bytes.fromhex("75112808000080414141607d2141414141"),
        bytes.fromhex("79112800000000414141607d2141414141"),
        bytes.fromhex("79112808000080414141607dff41414141"),
        bytes.fromhex("79112808000080414141e07f2141414141"),
    ),
)
def test_level2_decoder_rejects_malformed_or_unsupported_blocks(encoded):
    with pytest.raises((TypeError, ValueError)):
        decompress_level2(encoded, 40)


def test_level2_decoder_rejects_length_mismatch():
    encoded = bytes.fromhex(VECTORS[0][0])

    with pytest.raises(ValueError, match="gute frame"):
        decompress_level2(encoded, 39)
