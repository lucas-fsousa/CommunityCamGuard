import threading
from dataclasses import replace
from types import SimpleNamespace

import pytest

from backend.app.diagnostics import yoosee_shared_decode as module
from backend.app.diagnostics.video_delivery_sample import VideoDeliverySample
from backend.app.drivers.yoosee.p2p.contracts import P2PProbeError
from backend.app.drivers.yoosee.p2p.stream_protocol import unpack_v1_encoding_header
from backend.app.drivers.yoosee.p2p.v1_receive import V1Record
from backend.app.drivers.yoosee.p2p.video_delivery import VideoDelivery
from tests.test_captured_media import header
from tests.test_hevc_recovery import idr


@pytest.mark.parametrize("fault", [None, "epoch", "payload"])
def test_real_consumer_thread_has_bounded_sample_and_no_audio(monkeypatch, fault):
    queue = VideoDelivery()
    queue.consume(V1Record(encoding=unpack_v1_encoding_header(header()), video=idr(),
                           video_timestamp=1, audio=(b"private-audio",)))
    frame = queue.take()
    second = replace(frame, restart_decoder=False, timestamp=2)
    if fault == "epoch": second = replace(second, epoch=frame.epoch + 1)
    if fault == "payload": second = replace(second, payload=b"invalid-private-video")
    frames = iter([frame, second])
    done = threading.Event()
    def take():
        value = next(frames, None)
        if value is None:
            queue.close()
            done.set()
        return value
    monkeypatch.setattr(queue, "take", take)
    consumer = VideoDeliverySample(queue)
    original = consumer.sample.close
    def closed():
        original()
        done.set()
    monkeypatch.setattr(consumer.sample, "close", closed)
    try:
        consumer.start()
        assert done.wait(2)
        consumer.join()
        assert consumer.failed == (fault is not None)
        if not fault:
            assert consumer.sample.frames == 2
            assert b"private-audio" not in consumer.sample.data
        else:
            assert consumer.sample.closed and not consumer.sample.data
    finally:
        consumer.close()
    assert not consumer._thread.is_alive() and not consumer.sample.data


@pytest.mark.parametrize("fault", [None, "tools", "source", "cleanup", "decode"])
def test_decode_only_after_source_cleanup_and_consumer_join(monkeypatch, fault):
    events = []
    def tools():
        if fault == "tools": raise P2PProbeError("tools")
    monkeypatch.setattr(module, "require_decoder_tools", tools)
    class Consumer:
        failed = False
        sample = object()
        def __init__(self, queue): self.queue = queue
        def start(self): events.append("start")
        def join(self): events.append("join")
        def close(self): events.append("clear")
    monkeypatch.setattr(module, "VideoDeliverySample", Consumer)
    result = SimpleNamespace(reason="media_complete", tcp=SimpleNamespace(close_acknowledged=True),
                             media=SimpleNamespace(broker_release=fault != "cleanup"))
    def source(**kwargs):
        assert isinstance(kwargs["video_delivery"], VideoDelivery)
        events.append("source_closed")
        if fault == "source": raise P2PProbeError("source")
        return result
    monkeypatch.setattr(module, "run_reviewed_shared_media", source)
    def decode(sample, **kwargs):
        assert events == ["start", "source_closed", "join"]
        events.append("decode")
        if fault == "decode": raise P2PProbeError("decode")
        return "decoded"
    monkeypatch.setattr(module, "decode_sample", decode)
    if fault:
        with pytest.raises(P2PProbeError):
            module.run_reviewed_shared_decode(camera_id="camera", device_id="123")
    else:
        assert module.run_reviewed_shared_decode(camera_id="camera", device_id="123").video == "decoded"
    assert not events if fault == "tools" else events[-1] == "clear"
