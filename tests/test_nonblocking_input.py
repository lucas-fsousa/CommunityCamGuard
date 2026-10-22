import os

import pytest

from backend.app.media.nonblocking_input import MAX_PENDING_BYTES, NonblockingInput


@pytest.fixture
def pipe():
    read, write = os.pipe()
    os.set_blocking(read, False)
    now = [0.0]
    writer = NonblockingInput(write, clock=lambda: now[0])
    try:
        yield read, writer, now
    finally:
        writer.close()
        os.close(read)


def test_partial_writes_preserve_exact_access_unit_and_backpressure(pipe):
    read, writer, _ = pipe
    payload = bytes(range(256)) * 4096
    assert writer.offer(payload)
    assert not writer.offer(b"must-not-interleave")
    received = bytearray()
    while writer.pending_bytes:
        assert 0 < writer.pump() <= 16384
        received.extend(os.read(read, 65536))
    assert received == payload
    assert writer.pump() == 0
    assert writer.offer(b"next-frame")
    writer.pump()
    assert os.read(read, 65536) == b"next-frame"


def test_full_pipe_never_blocks_and_absolute_deadline_retires(pipe):
    _, writer, now = pipe
    writer.offer(b"x" * MAX_PENDING_BYTES)
    for _ in range(512):
        if writer.pump() == 0:
            break
    else:
        pytest.fail("expected bounded pipe capacity")
    assert writer.pending_bytes > 0
    now[0] = 1.0
    with pytest.raises(TimeoutError):
        writer.pump()
    assert writer.closed and writer.pending_bytes == 0
    writer.close()


def test_closed_decoder_retires_pending_bytes():
    read, write = os.pipe()
    os.close(read)
    writer = NonblockingInput(write)
    writer.offer(b"private-video")
    with pytest.raises(BrokenPipeError):
        writer.pump()
    assert writer.closed and writer.pending_bytes == 0


@pytest.mark.parametrize("payload", [b"", b"x" * (MAX_PENDING_BYTES + 1), bytearray(b"x")],
                         ids=["empty", "oversized", "mutable"])
def test_invalid_offer_retains_no_frame(pipe, payload):
    _, writer, _ = pipe
    with pytest.raises(ValueError):
        writer.offer(payload)
    assert writer.pending_bytes == 0


@pytest.mark.parametrize("instant", [-1.0, float("nan"), float("inf")])
def test_invalid_clock_fails_closed(pipe, instant):
    _, writer, now = pipe
    writer.offer(b"frame")
    now[0] = instant
    with pytest.raises(ValueError):
        writer.pump()
    assert writer.closed and writer.pending_bytes == 0


def test_cancellation_closes_owned_fd_once(pipe):
    _, writer, _ = pipe
    writer.offer(b"frame")
    writer.close()
    writer.close()
    assert writer.pending_bytes == 0
    with pytest.raises(ValueError):
        writer.offer(b"later")


def test_continuous_input_has_no_sample_frame_or_lifetime_limit(pipe):
    read, writer, now = pipe
    for index in range(4500):
        now[0] = index * 0.2
        payload = index.to_bytes(4, "big") * 256
        assert writer.offer(payload)
        assert writer.pump() == len(payload)
        assert os.read(read, 4096) == payload
        assert writer.pending_bytes == 0
    assert not writer.closed


def test_partial_progress_does_not_extend_deadline(pipe):
    read, writer, now = pipe
    writer.offer(b"x" * MAX_PENDING_BYTES)
    for instant in (0.0, 0.4, 0.8):
        now[0] = instant
        assert writer.pump() > 0
        os.read(read, 65536)
    now[0] = 1.0
    with pytest.raises(TimeoutError):
        writer.pump()
    assert writer.closed


def test_failed_initialization_releases_owned_descriptor():
    read, write = os.pipe()
    try:
        with pytest.raises(ValueError):
            NonblockingInput(write, stall_timeout=0)
        with pytest.raises(OSError):
            os.fstat(write)
    finally:
        os.close(read)
