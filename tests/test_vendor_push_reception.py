"""Out-of-order local callbacks, independent of camera/session credentials."""

import pytest

from backend.app.drivers.yoosee.p2p.push_framing import PushFrameError, PushTcpFramer
from backend.app.drivers.yoosee.p2p.push_reception import PushReception

FRAME = bytes.fromhex("0307000001000000000000000000000000000000") + b"x"


def test_old_bytes_cannot_complete_new_connection():
    receiver = PushReception()
    old = receiver.begin()
    assert receiver.receive(old, FRAME[:10]) == []
    new = receiver.begin()
    assert receiver.receive(old, FRAME[10:]) == []
    assert receiver.receive(new, FRAME) == [FRAME]


def test_stale_eof_does_not_close_replacement():
    receiver = PushReception()
    old = receiver.begin()
    new = receiver.begin()
    receiver.eof(old)
    assert receiver.receive(new, FRAME) == [FRAME]


def test_old_malformed_read_does_not_poison_replacement():
    receiver = PushReception()
    old = receiver.begin()
    new = receiver.begin()
    assert receiver.receive(old, b"invalid" * 10000) == []
    assert receiver.receive(new, FRAME) == [FRAME]


def test_malformed_current_read_retires_generation():
    receiver = PushReception()
    current = receiver.begin()
    with pytest.raises(PushFrameError):
        receiver.receive(current, b"\xff" + FRAME[1:])
    assert receiver.receive(current, FRAME) == []
    new = receiver.begin()
    assert receiver.receive(new, FRAME) == [FRAME]


def test_truncated_eof_retires_generation_even_on_error():
    receiver = PushReception()
    current = receiver.begin()
    receiver.receive(current, FRAME[:10])
    with pytest.raises(PushFrameError, match="truncated"):
        receiver.eof(current)
    assert receiver.receive(current, FRAME) == []
    receiver.eof(current)


def test_cancel_is_idempotent_and_drops_future_callbacks():
    receiver = PushReception()
    current = receiver.begin()
    receiver.receive(current, FRAME[:10])
    receiver.cancel()
    receiver.cancel()
    assert receiver.receive(current, FRAME) == []
    receiver.eof(current)


def test_clean_eof_retires_generation():
    receiver = PushReception()
    current = receiver.begin()
    assert receiver.receive(current, FRAME) == [FRAME]
    receiver.eof(current)
    assert receiver.receive(current, FRAME) == []


def test_foreign_generation_is_never_accepted():
    receiver = PushReception()
    other = PushReception()
    current = receiver.begin()
    foreign = other.begin()
    assert receiver.receive(foreign, FRAME) == []
    receiver.eof(foreign)
    assert receiver.receive(current, FRAME) == [FRAME]


def test_abort_clears_partial_buffer_and_is_terminal():
    decoder = PushTcpFramer()
    decoder.feed(FRAME[:10])
    decoder.abort()
    decoder.abort()
    assert decoder.buffered_bytes == 0
    with pytest.raises(PushFrameError, match="closed"):
        decoder.feed(FRAME)
