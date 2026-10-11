from backend.app.drivers.yoosee.p2p.reception_timing import ArrivalTiming, ReceptionTiming


def test_no_media_is_unknown_not_instant_startup():
    timer = ArrivalTiming(10)
    assert timer.finish(12) == ReceptionTiming(None, None, 0, 0, 2000)


def test_startup_gap_and_trailing_silence_are_separate():
    timer = ArrivalTiming(10)
    timer.ready(10.2)
    timer.ready(11)  # Repeated readiness cannot reset startup.
    for at in (10.5, 10.5, 10.6, 11.8):
        timer.video(at)
    assert timer.finish(13) == ReceptionTiming(200, 500, 1200, 1200, 3000)


def test_close_and_late_media_cannot_change_frozen_measurement():
    timer = ArrivalTiming(10)
    timer.video(11)
    result = timer.finish(12)
    timer.ready(13)
    timer.video(20)
    assert timer.finish(21) is result
    assert result == ReceptionTiming(None, 1000, 0, 1000, 2000)


def test_many_arrivals_retain_no_history():
    timer = ArrivalTiming(0)
    for index in range(10000):
        timer.video(index / 100)
    assert timer.finish(100).max_video_gap_ms == 10
    assert not any(isinstance(value, (list, dict, bytes, bytearray)) for value in vars(timer).values())
