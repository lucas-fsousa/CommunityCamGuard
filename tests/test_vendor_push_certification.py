"""Independent SDK certification wire vectors; never send packets."""

import struct

import pytest

from backend.app.drivers.yoosee.p2p.push_certification import build_push_certification


def values():
    return dict(push_id=0x12345678, access_id=0x0102030405060708,
                session_type=2, session_id=0x4321, token=b"abcd", device_id=0x1122334455667788)


def test_single_terminal_vector():
    frame = build_push_certification(**values())
    assert len(frame) == 40
    assert frame[:6] == bytes.fromhex("030600001400")
    assert frame[8:20] == bytes.fromhex("785634120807060504030201")
    assert frame[20:] == bytes.fromhex("02010c0004002143616263648877665544332211")
    # Body words 0102, 000c, 0004, 4321, rotated by 0,1,2,3; XOR body size.
    assert struct.unpack_from("<H", frame, 6)[0] == (0x0102 ^ 0x18 ^ 0x10 ^ 0x190A ^ 20)


@pytest.mark.parametrize("field,bits", [("push_id", 32), ("access_id", 64),
                                       ("session_type", 8), ("session_id", 16), ("device_id", 64)])
@pytest.mark.parametrize("kind", ["negative", "overflow", "bool", "string"])
def test_invalid_fields(field, bits, kind):
    value = {"negative": -1, "overflow": 1 << bits, "bool": True, "string": "1"}[kind]
    with pytest.raises(ValueError):
        build_push_certification(**(values() | {field: value}))


@pytest.mark.parametrize("token", [b"", b"a", b"a" * 4097, bytearray(b"ab"), "ab"])
def test_unsafe_token(token):
    with pytest.raises(ValueError):
        build_push_certification(**(values() | {"token": token}))


@pytest.mark.parametrize("size", [2, 4096])
def test_token_boundaries(size):
    frame = build_push_certification(**(values() | {"token": b"a" * size}))
    assert len(frame) == size + 36
    assert struct.unpack_from("<H", frame, 4)[0] == size + 16
    assert frame[28:28 + size] == b"a" * size


def test_zero_device_rejected():
    with pytest.raises(ValueError):
        build_push_certification(**(values() | {"device_id": 0}))
