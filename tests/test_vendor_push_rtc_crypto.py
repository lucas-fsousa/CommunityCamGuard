"""Synthetic vectors independently calculated from SDK ARM64 block arithmetic.

JS unsigned-u32 transcription of helpers 0x261ea4/0x262390, using the separately
derived schedule for key 00..07. Not native execution or captured-media evidence.
"""

import struct

import pytest

from backend.app.drivers.yoosee.p2p import push_rtc_crypto
from backend.app.drivers.yoosee.p2p.crypto import RC5

KEY = bytes.fromhex("0001020304050607")
VECTORS = [
    ("0000000000000000", "d27a3dc11ed9c1a2"),
    ("ffffffffffffffff", "3858a1a739d1dd56"),
    ("0001020304050607", "3a8a4fe3106da52a"),
    # Cancels both whitening words, exercising zero rotation counts.
    ("322f75e585487801", "47c1b55a9c3b72e0"),
]


@pytest.mark.parametrize("plain,encrypted", VECTORS)
def test_independent_block_vectors(plain, encrypted):
    cipher = RC5(KEY, rounds=6, w=32)
    assert cipher.encrypt_block(bytes.fromhex(plain)) == bytes.fromhex(encrypted)
    assert cipher.decrypt_block(bytes.fromhex(encrypted)) == bytes.fromhex(plain)


@pytest.mark.parametrize("kind,offset", [
    (0x80, 24), (0x81, 8), (0x82, 8), (0x83, 8), (0xF1, 20), (0xF2, 20),
])
@pytest.mark.parametrize("tail_size", range(8))
@pytest.mark.parametrize("blocks", [0, 1, 4])
def test_complete_records_preserve_prefix_tail_and_input(kind, offset, tail_size, blocks):
    tail = bytes(range(tail_size))
    prefix = struct.pack("<HHI", kind, 0xABCD, offset - 8 + blocks * 8 + tail_size)
    prefix += b"\xa5" * (offset - 8)
    encrypted = prefix + b"".join(bytes.fromhex(v[1]) for v in VECTORS[:blocks]) + tail
    expected = prefix + b"".join(bytes.fromhex(v[0]) for v in VECTORS[:blocks]) + tail
    original = bytes(bytearray(encrypted))
    result = push_rtc_crypto.decrypt_selective_rtc(encrypted, key=KEY)
    assert result == expected
    assert isinstance(result, bytes)
    assert encrypted == original


@pytest.mark.parametrize("key", [b"", b"1234567", b"123456789", bytearray(KEY), None, "12345678"])
def test_invalid_keys(key):
    with pytest.raises(ValueError, match="key"):
        push_rtc_crypto.decrypt_selective_rtc(struct.pack("<HHI", 0x81, 0, 0), key=key)


@pytest.mark.parametrize("frame", [
    b"", struct.pack("<HHI", 0x81, 0, 8), struct.pack("<HHI", 0x99, 0, 0),
    struct.pack("<HHI", 0x80, 0, 0), bytearray(8), bytes(0x8400),
])
def test_invalid_records_rejected_before_cipher(monkeypatch, frame):
    def forbidden(*args, **kwargs):
        pytest.fail("cipher must not be constructed for invalid records")
    monkeypatch.setattr(push_rtc_crypto, "RC5", forbidden)
    with pytest.raises(ValueError):
        push_rtc_crypto.decrypt_selective_rtc(frame, key=KEY)
