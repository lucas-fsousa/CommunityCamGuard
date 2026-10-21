from dataclasses import replace

import pytest

from backend.app.drivers.yoosee.p2p import video_delivery as module
from backend.app.drivers.yoosee.p2p.stream_protocol import unpack_v1_encoding_header
from backend.app.drivers.yoosee.p2p.v1_receive import V1Record
from tests.test_captured_media import header
from tests.test_hevc_recovery import idr, nal


def delivery():
    queue = module.VideoDelivery()
    queue.consume(V1Record(encoding=unpack_v1_encoding_header(header())))
    return queue


def test_delivery_requires_configured_idr_and_omits_audio():
    queue = delivery()
    queue.consume(V1Record(video=nal(1), audio=(b"secret-audio",)))
    assert queue.take() is None
    queue.consume(V1Record(video=idr(), video_timestamp=10))
    queue.consume(V1Record(video=nal(1), video_timestamp=11))
    frame = queue.take()
    assert frame.payload == idr() and frame.restart_decoder
    assert "payload" not in repr(frame) and "secret" not in repr(frame)
    assert queue.take().restart_decoder is False
    assert queue.buffered_bytes == 0 and queue.take() is None


@pytest.mark.parametrize("limit", ["frames", "bytes"])
def test_slow_consumer_discards_backlog_and_waits_for_new_idr(monkeypatch, limit):
    if limit == "frames": monkeypatch.setattr(module, "MAX_FRAMES", 2)
    else: monkeypatch.setattr(module, "MAX_BYTES", len(idr()) + len(nal(1)))
    queue = delivery()
    queue.consume(V1Record(video=idr(), video_timestamp=1))
    queue.consume(V1Record(video=nal(1), video_timestamp=2))
    queue.consume(V1Record(video=nal(1), video_timestamp=3))
    assert queue.take() is None and queue.buffered_bytes == 0
    queue.consume(V1Record(video=idr(), video_timestamp=4))
    frame = queue.take()
    assert frame.restart_decoder and frame.epoch == 2


def test_invalid_header_and_shutdown_release_all_references():
    queue = delivery()
    queue.consume(V1Record(video=idr(), video_timestamp=1))
    queue.consume(V1Record(encoding=replace(unpack_v1_encoding_header(header()), video_codec=99)))
    queue.consume(V1Record(video=idr(), video_timestamp=2))
    assert queue.take() is None and queue.buffered_bytes == 0
    queue.close()
    queue.close()
    queue.consume(V1Record(encoding=unpack_v1_encoding_header(header()), video=idr()))
    assert queue.closed and queue.take() is None


def test_timestamp_regression_restarts_without_old_queued_frames():
    queue = delivery()
    queue.consume(V1Record(video=idr(), video_timestamp=10))
    queue.consume(V1Record(video=idr(), video_timestamp=1))
    frame = queue.take()
    assert frame.timestamp == 1 and frame.restart_decoder and frame.epoch == 2
    assert queue.take() is None


def test_oversized_frame_is_not_retained(monkeypatch):
    queue = delivery()
    monkeypatch.setattr(module, "MAX_BYTES", len(idr()) - 1)
    queue.consume(V1Record(video=idr()))
    assert queue.buffered_bytes == 0 and queue.take() is None
