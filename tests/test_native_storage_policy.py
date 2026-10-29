"""Storage policy changes retire one decoder, not the native camera account."""

import threading
from unittest.mock import Mock

import pytest

from backend.app.drivers.yoosee import native_video_source as module
from backend.app.drivers.yoosee.p2p.stream_protocol import unpack_v1_encoding_header
from backend.app.drivers.yoosee.p2p.v1_receive import V1Record
from backend.app.drivers.yoosee.p2p.video_consumer import VideoConsumer
from backend.app.media.fragmented_mp4 import Mp4Chunk
from backend.app.media.mp4_fanout import StreamClosedError
from backend.app.recording.recorder import Recorder
from backend.app.recording.storage import StorageMonitor
from tests.test_av_delivery import ADTS
from tests.test_captured_media import header
from tests.test_hevc_recovery import idr, nal


@pytest.fixture
def pipeline(tmp_path, monkeypatch):
    allowed = [True]
    children = []
    session = Mock()
    session_factory = Mock(return_value=session)
    monkeypatch.setattr(module, "SharedReadSession", session_factory)

    class Child:
        pending_bytes = 0
        closed = False
        fail_close = False

        def __init__(self, **kwargs):
            assert not children or children[-1].closed
            self.options = kwargs
            children.append(self)

        def offer(self, *args, **kwargs): return True
        def step(self): pass

        def close(self):
            if self.fail_close:
                raise RuntimeError("unconfirmed child retirement")
            self.closed = True

    monkeypatch.setattr(module, "NativeAvDecoder", Child)
    source = module.NativeVideoSource("test", "123", native_audio=True,
        archive=tmp_path / "%Y%m%d_%H%M%S.mp4", archive_segment_seconds=300,
        saving_allowed=lambda: allowed[0])
    consumer = VideoConsumer(source._delivery, factory=source._decoder)
    try:
        yield source, consumer, allowed, children, session_factory
    finally:
        for child in children:
            child.fail_close = False
        consumer.close()
        source.stop()


def frame(source, timestamp, *, key=True):
    source._delivery.consume(V1Record(encoding=unpack_v1_encoding_header(header()),
        video=idr() if key else nal(1), video_timestamp=timestamp))


def test_pause_resume_preserves_receiver_and_replaces_only_after_close(pipeline):
    source, consumer, allowed, children, session_factory = pipeline
    frame(source, 1_000_000)
    consumer.step()
    original = children[0].options["archive"]
    source._hub.publish(Mp4Chunk(True, b"synthetic-init"))
    source._hub.publish(Mp4Chunk(False, b"synthetic-fragment"))
    old_reader = source.subscribe()
    source._delivery.consume(V1Record(audio=(ADTS,), audio_timestamp=1_100_000))
    frame(source, 1_200_000, key=False)
    allowed[0] = False
    source._apply_recording_policy(consumer)
    assert children[0].closed and consumer.decoder is None
    assert not consumer.closed and not source._delivery.closed
    assert source._delivery.buffered_bytes == 0 and source._delivery.take_audio() is None
    with pytest.raises(StreamClosedError): old_reader.take()
    # Dependent frames cannot restart the child after discarding the backlog.
    frame(source, 1_300_000, key=False)
    consumer.step()
    assert len(children) == 1
    frame(source, 2_000_000)
    consumer.step()
    assert children[1].options["archive"] is None  # live A/V remains enabled
    source._apply_recording_policy(consumer)
    assert len(children) == 2 and not children[1].closed
    allowed[0] = True
    source._apply_recording_policy(consumer)
    frame(source, 3_000_000)
    consumer.step()
    assert children[1].closed and len(children) == 3
    assert children[2].options["archive"] != original
    assert children[2].options["archive_segment_seconds"] == 300
    session_factory.assert_called_once()
    source._session.stop.assert_not_called()
    source._session.run.assert_not_called()  # no camera/network in this test


def test_already_paused_never_opens_initial_archive(pipeline, monkeypatch):
    source, consumer, allowed, children, _ = pipeline
    prepare = Mock(side_effect=AssertionError("paused recording must not prepare paths"))
    monkeypatch.setattr(source._archive_dirs, "prepare", prepare)
    allowed[0] = False
    source._apply_recording_policy(consumer)
    frame(source, 1_000_000)
    consumer.step()
    assert children[0].options["archive"] is None
    prepare.assert_not_called()


def test_unconfirmed_retirement_cannot_create_live_replacement(pipeline):
    source, consumer, allowed, children, _ = pipeline
    frame(source, 1_000_000)
    consumer.step()
    children[0].fail_close = True
    allowed[0] = False
    with pytest.raises(RuntimeError, match="unconfirmed"):
        source._apply_recording_policy(consumer)
    assert consumer.decoder is children[0] and len(children) == 1
    assert source._recording_allowed is True  # not falsely acknowledged


@pytest.mark.parametrize("invalid", [None, 0, 1, "true"])
def test_bad_policy_result_is_not_treated_as_permission(pipeline, invalid):
    source, consumer, allowed, children, _ = pipeline
    allowed[0] = invalid
    with pytest.raises(ValueError, match="boolean"):
        source._apply_recording_policy(consumer)
    assert not children


def test_server_disk_full_hysteresis_reaches_existing_archive_plan(tmp_path, monkeypatch):
    recorder = Recorder()
    spec = recorder.native_archive("cam_" + "a" * 24)
    monitor = StorageMonitor(recorder, path=tmp_path, full_percent=98, resume_percent=90)
    used = [99]
    monkeypatch.setattr(monitor, "_used_percent", lambda: (100, used[0], 100-used[0], used[0]))
    assert spec.saving_allowed()
    monitor.check()
    assert recorder.paused and not spec.saving_allowed()
    used[0] = 95
    monitor.check()
    assert not spec.saving_allowed()
    used[0] = 89
    monitor.check()
    assert not recorder.paused and spec.saving_allowed()


def test_policy_is_lazy_and_requires_archive(tmp_path):
    policy = Mock(return_value=True)
    source = module.NativeVideoSource("test", "123", native_audio=True,
        archive=tmp_path / "%Y%m%d_%H%M%S.mp4", saving_allowed=policy)
    policy.assert_not_called()
    assert source._thread is None and not list(tmp_path.iterdir())
    source.stop()
    with pytest.raises(ValueError, match="recording policy"):
        module.NativeVideoSource("test", "123", saving_allowed=policy)


def test_actual_consumer_loop_observes_pause_and_resume_without_account_restart(pipeline):
    source, _, allowed, children, session_factory = pipeline
    worker = threading.Thread(target=source._consume)

    def wait_until(predicate):
        for _ in range(1000):
            if predicate():
                return
            assert worker.is_alive(), source.diagnostics()
            threading.Event().wait(0.002)
        pytest.fail("consumer did not observe the recording policy")

    worker.start()
    try:
        frame(source, 1_000_000)
        wait_until(lambda: len(children) == 1)
        allowed[0] = False
        wait_until(lambda: children[0].closed and not source._recording_allowed)
        frame(source, 2_000_000)
        wait_until(lambda: len(children) == 2)
        assert children[1].options["archive"] is None
        allowed[0] = True
        wait_until(lambda: children[1].closed and source._recording_allowed)
        # AAC received before the new IDR is drained, not allowed to overflow.
        source._delivery.consume(V1Record(audio=(ADTS,) * 100, audio_timestamp=2_100_000))
        wait_until(lambda: source._delivery._audio_bytes == 0)
        frame(source, 3_000_000)
        wait_until(lambda: len(children) == 3)
        assert children[2].options["archive"] is not None
        session_factory.assert_called_once()
        source._session.run.assert_not_called()
        assert not source._failed.is_set()
    finally:
        source.stop()
        worker.join(timeout=3)
    assert not worker.is_alive() and all(child.closed for child in children)
