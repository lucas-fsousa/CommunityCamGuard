"""Synthetic codec tests only: never create sockets or use real session material."""

import struct

import pytest

from backend.app.drivers.yoosee.p2p.push_teardown import MAX_TOKEN_BYTES, build_push_hangup


def values():
    return dict(push_id=0x12345678, access_id=0x0102030405060708,
                session_type=2, session_id=0x4321, token=b"\xaa\xbb\xcc\xdd")


def test_sdk_hangup_layout_and_independent_checksum():
    frame = build_push_hangup(**values())
    assert len(frame) == 30
    assert frame[:6] == bytes.fromhex("030b00000a00")
    assert frame[8:20] == bytes.fromhex("785634120807060504030201")
    assert frame[20:] == bytes.fromhex("000204002143aabbccdd")
    # Four LE words: 0200, 0004, 4321, bbaa; rotate by 0,1,2,3, then XOR body length.
    expected = 0x0200 ^ 0x0008 ^ 0x0C85 ^ 0xDD55 ^ 10
    assert struct.unpack_from("<H", frame, 6)[0] == expected


@pytest.mark.parametrize("field,bits", [("push_id", 32), ("access_id", 64),
                                       ("session_type", 8), ("session_id", 16)])
@pytest.mark.parametrize("kind", ["negative", "overflow", "bool", "string"])
def test_invalid_fields_rejected(field, bits, kind):
    args = values()
    args[field] = {"negative": -1, "overflow": 1 << bits, "bool": True, "string": "1"}[kind]
    with pytest.raises(ValueError):
        build_push_hangup(**args)


@pytest.mark.parametrize("token", [b"", b"a", bytearray(b"ab"), "ab", b"a" * (MAX_TOKEN_BYTES + 1)])
def test_invalid_tokens_rejected(token):
    with pytest.raises(ValueError):
        build_push_hangup(**(values() | {"token": token}))


@pytest.mark.parametrize("size", [2, MAX_TOKEN_BYTES])
def test_bounded_token_edges(size):
    frame = build_push_hangup(**(values() | {"token": b"a" * size}))
    assert len(frame) == 26 + size
    assert struct.unpack_from("<H", frame, 4)[0] == 6 + size
