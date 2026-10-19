import logging

from backend.app.drivers.yoosee.audio_timing import DeliveryTiming


def test_delivery_timing_distinguishes_buffered_send_from_deadline_miss():
    stats = DeliveryTiming()
    stats.sent(started=1.0, finished=1.03, playback_deadline=1.10)
    assert stats.late_packets == 0
    stats.sent(started=2.0, finished=2.15, playback_deadline=2.10)
    assert stats.packets == 2
    assert stats.late_packets == 1
    assert round(stats.max_lateness_ms) == 50
    assert round(stats.max_send_ms) == 150


def test_summary_is_content_free_and_sanitizes_identity(caplog):
    stats = DeliveryTiming()
    with caplog.at_level(logging.INFO):
        stats.report("rtsp://secret:password@host\nforged", elapsed=1.5,
                     opened=False, released=True)
    assert len(caplog.records) == 1
    assert "camera=unknown" in caplog.text
    assert "elapsed_ms=1500.0" in caplog.text
    assert "secret" not in caplog.text and "password" not in caplog.text
    assert "forged" not in caplog.text


def test_summary_retains_only_opaque_camera_id(caplog):
    with caplog.at_level(logging.INFO):
        DeliveryTiming().report("cam_0123456789abcdef01234567", elapsed=0,
                                opened=True, released=False)
    assert "camera=cam_0123456789abcdef01234567" in caplog.text
    assert "released=False" in caplog.text
