import pytest

from backend.app.media.fragmented_mp4 import MAX_BYTES, MAX_CHUNKS, Mp4Chunk
from backend.app.media.mp4_fanout import (
    MAX_READERS,
    Mp4Fanout,
    SlowConsumerError,
    StreamClosedError,
)


def ready():
    hub = Mp4Fanout()
    init = Mp4Chunk(True, b"initialization")
    frame = Mp4Chunk(False, b"fragment")
    hub.publish(init)
    hub.publish(frame)
    return hub, init, frame


def test_readers_share_same_bytes_and_join_at_latest_fragment():
    hub, init, _ = ready()
    latest = Mp4Chunk(False, b"latest")
    hub.publish(latest)
    first, second = hub.subscribe(), hub.subscribe()
    assert first.take() is init and second.take() is init
    assert first.take() is latest and second.take() is latest
    assert first.take() is None and second.take() is None
    assert hub.buffered_bytes == len(init.data) + len(b"fragmentlatest")


def test_slow_reader_is_disconnected_without_stalling_fast_reader():
    hub, init, _ = ready()
    slow, fast = hub.subscribe(), hub.subscribe()
    assert fast.take() is init
    fast.take()
    for number in range(MAX_CHUNKS + 2):
        fragment = Mp4Chunk(False, bytes([number + 1]))
        hub.publish(fragment)
        assert fast.take() is fragment
    with pytest.raises(SlowConsumerError):
        slow.take()
    assert hub.reader_count == 1 and fast.take() is None
    assert hub.buffered_bytes == len(init.data) + MAX_CHUNKS


def test_byte_limit_evicts_and_disconnects_only_lagging_reader():
    hub, init, _ = ready()
    slow = hub.subscribe()
    huge = Mp4Chunk(False, b"x" * (MAX_BYTES - len(init.data)))
    hub.publish(huge)
    assert hub.buffered_bytes == MAX_BYTES
    with pytest.raises(SlowConsumerError):
        slow.take()
    reader = hub.subscribe()
    assert reader.take() is init and reader.take() is huge


def test_reader_capacity_and_revocation_are_independent_of_producer():
    hub, _, frame = ready()
    readers = [hub.subscribe() for _ in range(MAX_READERS)]
    with pytest.raises(StreamClosedError, match="capacity"):
        hub.subscribe()
    readers[0].close()
    readers[0].close()
    with pytest.raises(StreamClosedError):
        readers[0].take()
    replacement = hub.subscribe()
    replacement.take()
    assert replacement.take() is frame
    hub.publish(Mp4Chunk(False, b"next"))
    assert replacement.take().data == b"next"


def test_epoch_closure_invalidates_all_subscribers_and_releases_ring():
    hub, _, _ = ready()
    reader = hub.subscribe()
    hub.close()
    hub.close()
    assert hub.buffered_bytes == 0 and hub.reader_count == 0
    with pytest.raises(StreamClosedError):
        reader.take()
    with pytest.raises(StreamClosedError):
        hub.subscribe()
    with pytest.raises(StreamClosedError):
        hub.publish(Mp4Chunk(True, b"new-epoch"))


def test_second_initialization_fails_closed_instead_of_mixing_epochs():
    hub, _, _ = ready()
    reader = hub.subscribe()
    with pytest.raises(ValueError):
        hub.publish(Mp4Chunk(True, b"new-epoch"))
    with pytest.raises(StreamClosedError):
        reader.take()
    assert hub.buffered_bytes == 0


def test_not_ready_never_exposes_media_without_init():
    hub = Mp4Fanout()
    with pytest.raises(StreamClosedError):
        hub.subscribe()
    with pytest.raises(ValueError):
        hub.publish(Mp4Chunk(False, b"fragment"))


def test_long_run_does_not_accumulate_history_or_copy_per_reader():
    hub, init, _ = ready()
    readers = [hub.subscribe() for _ in range(MAX_READERS)]
    for reader in readers:
        reader.take()
        reader.take()
    chunk = Mp4Chunk(False, b"x" * 1024)
    for _ in range(4500):
        hub.publish(chunk)
        for reader in readers:
            assert reader.take() is chunk
    assert hub.reader_count == MAX_READERS
    assert hub.buffered_bytes == len(init.data) + MAX_CHUNKS * len(chunk.data)


def test_oversized_fragment_retires_hub_and_current_readers():
    hub, _, _ = ready()
    reader = hub.subscribe()
    with pytest.raises(ValueError):
        hub.publish(Mp4Chunk(False, b"x" * MAX_BYTES))
    assert hub.buffered_bytes == 0
    with pytest.raises(StreamClosedError):
        reader.take()
