"""Reply metadata does not grant a capability or change AV/talk admission."""

import struct

import pytest

from backend.app.drivers.yoosee.p2p.microphone_reply import (
    microphone_reply,
    wrapped_microphone_replies,
)
from backend.app.drivers.yoosee.p2p.stream_protocol import V1_MAGIC, pack_v1_sequence_user_data


def body(*, command=0x32, origin=0, flags=0, result=0):
    return struct.pack('<BBHIQ', origin, command, flags, 123, result)


@pytest.mark.parametrize('result', [0, 0x4eb1, 5])
def test_only_exact_scalar_result_is_decoded(result):
    reply = microphone_reply(body(result=result))
    assert reply is not None
    assert (reply.timestamp, reply.payload_size, reply.result) == (123, 8, result)
    short = microphone_reply(body()[:9])
    assert short is not None and short.result is None


@pytest.mark.parametrize('data', [b'', bytes(7), body(command=1), body(origin=2),
                                  body(flags=4), body()+b'x', bytes(1025)])
def test_unknown_and_malformed_messages_provide_no_reply(data):
    assert microphone_reply(data) is None


def test_both_known_v1_wrappers_and_bad_wrapper():
    data = body()
    compact = V1_MAGIC + struct.pack('<HH', 0x0200, len(data)) + data
    assert wrapped_microphone_replies(compact) == (microphone_reply(data),)
    assert wrapped_microphone_replies(pack_v1_sequence_user_data(data)) == (microphone_reply(data),)
    assert not wrapped_microphone_replies(compact[:-1])
    assert not wrapped_microphone_replies(bytes(1025))
