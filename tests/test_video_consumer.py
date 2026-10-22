import pytest

from backend.app.drivers.yoosee.p2p.v1_receive import V1Record
from backend.app.drivers.yoosee.p2p.video_consumer import VideoConsumer
from tests.test_hevc_recovery import idr, nal
from tests.test_video_delivery import delivery


class Sink:
    def __init__(self):
        self.pending_bytes = 0
        self.frames = []
        self.closed = False
        self.stalled = False
        self.close_error = False

    def offer(self, payload):
        self.frames.append(payload)
        self.pending_bytes = len(payload)
        return True

    def step(self):
        if not self.stalled:
            self.pending_bytes = 0

    def close(self):
        if self.close_error:
            raise RuntimeError("shutdown unconfirmed")
        self.closed = True
        self.pending_bytes = 0


def test_epochs_replace_only_after_confirmed_retirement():
    queue = delivery()
    sinks = []

    def factory():
        assert not sinks or sinks[-1].closed
        sink = Sink()
        sinks.append(sink)
        return sink

    consumer = VideoConsumer(queue, factory=factory)
    for payload, timestamp in ((idr(), 10), (nal(1), 11), (idr(), 1), (nal(1), 2)):
        queue.consume(V1Record(video=payload, video_timestamp=timestamp))
        consumer.step()
    assert len(sinks) == 2
    assert sinks[0].frames == [idr(), nal(1)]
    assert sinks[1].frames == [idr(), nal(1)]
    consumer.close()
    consumer.close()
    assert queue.closed and all(s.closed for s in sinks)


def test_busy_decoder_leaves_next_frame_in_bounded_queue():
    queue = delivery()
    sink = Sink()
    consumer = VideoConsumer(queue, factory=lambda: sink)
    queue.consume(V1Record(video=idr(), video_timestamp=1))
    consumer.step()
    sink.stalled = True
    queue.consume(V1Record(video=nal(1), video_timestamp=2))
    consumer.step()
    assert sink.frames == [idr()] and queue.buffered_bytes == len(nal(1))
    queue.close()
    consumer.step()
    assert consumer.closed and sink.closed


def test_uncertain_shutdown_never_starts_second_decoder():
    queue = delivery()
    sink = Sink()
    calls = []

    def factory():
        calls.append(1)
        return sink

    consumer = VideoConsumer(queue, factory=factory)
    queue.consume(V1Record(video=idr(), video_timestamp=10))
    consumer.step()
    sink.close_error = True
    queue.consume(V1Record(video=idr(), video_timestamp=1))
    with pytest.raises(RuntimeError, match="shutdown unconfirmed"):
        consumer.step()
    assert calls == [1] and queue.closed and consumer.closed
    sink.close_error = False
    consumer.close()


def test_factory_failure_retires_source_queue():
    queue = delivery()
    queue.consume(V1Record(video=idr(), video_timestamp=1))

    def fail():
        raise RuntimeError("decoder unavailable")

    consumer = VideoConsumer(queue, factory=fail)
    with pytest.raises(RuntimeError):
        consumer.step()
    assert queue.closed and queue.buffered_bytes == 0 and consumer.closed
