"""Synthetic E4 correlation and field provenance; no network or credentials."""

import struct

import pytest

from backend.app.drivers.yoosee.p2p.push_context import parse_push_context
from backend.app.drivers.yoosee.p2p.push_teardown import build_push_hangup


def distribution(token=b"synthetic-token"):
    frame = bytearray(0x88 + len(token) + 2 + 16)
    frame[:2] = b"\x7e\xe4"
    struct.pack_into("<H", frame, 2, len(frame))
    frame[0x1D] = 2
    struct.pack_into("<H", frame, 0x1E, len(token))
    struct.pack_into("<II", frame, 0x28, 123, 456)
    struct.pack_into("<Q", frame, 0x38, 789)
    frame[0x88:0x88 + len(token)] = token
    frame[0x88 + len(token)] = 1
    return bytes(frame)


def parse(frame, **kwargs):
    return parse_push_context(frame, **(dict(expected_device_id=789, expected_link_id=123) | kwargs))


def test_context_sources_and_independent_local_session():
    context = parse(distribution())
    assert context is not None
    assert (context.push_id, context.session_type, context.token) == (456, 2, b"synthetic-token")
    assert repr(context) == "PushContext(session_type=2)"
    packet = build_push_hangup(push_id=context.push_id, session_type=context.session_type,
                               token=context.token, access_id=321, session_id=654)
    assert struct.unpack_from("<I", packet, 8)[0] == 456
    assert struct.unpack_from("<H", packet, 24)[0] == 654


@pytest.mark.parametrize("kwargs", [dict(expected_device_id=790), dict(expected_link_id=124)])
def test_wrong_session_rejected(kwargs):
    assert parse(distribution(), **kwargs) is None


@pytest.mark.parametrize("token", [b"", b"x"])
def test_unusable_token_rejected(token):
    assert parse(distribution(token)) is None


@pytest.mark.parametrize("value", [-1, 1 << 32, True, "123"])
def test_invalid_expected_link(value):
    with pytest.raises(ValueError):
        parse(distribution(), expected_link_id=value)


def test_every_truncated_prefix_rejected():
    frame = distribution()
    for length in range(len(frame)):
        assert parse(frame[:length]) is None


@pytest.mark.parametrize("offset,value", [(0, 0), (1, 0), (0x16, 0x10), (0x1F, 0xFF)])
def test_bad_envelope_ack_or_token_size(offset, value):
    frame = bytearray(distribution())
    frame[offset] = value
    assert parse(bytes(frame)) is None


def test_relay_bounds_checked_even_when_not_returned():
    frame = bytearray(distribution())
    frame[0x88 + len(b"synthetic-token")] = 255
    assert parse(bytes(frame)) is None
