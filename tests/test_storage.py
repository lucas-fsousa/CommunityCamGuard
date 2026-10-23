import pytest

from backend.app.recording.storage import ALERT, FULL, OK, StorageMonitor


class StubRecorder:
    def __init__(self):
        self.paused = False
        self.events = []

    def pause(self):
        self.paused = True
        self.events.append("pause")

    def resume(self):
        self.paused = False
        self.events.append("resume")


def _monitor(rec, pct, **kw):
    m = StorageMonitor(rec, alert_percent=kw.get("alert", 80),
                       full_percent=kw.get("full", 98), resume_percent=kw.get("resume", 95))
    # deterministic usage
    m._used_percent = lambda: (100, int(pct), 100 - int(pct), float(pct))
    return m


def test_ok_below_alert():
    rec = StubRecorder()
    assert _monitor(rec, 50).check().status == OK
    assert not rec.paused


def test_alert_between_thresholds():
    rec = StubRecorder()
    st = _monitor(rec, 85).check()
    assert st.status == ALERT and not rec.paused


def test_full_pauses_recorder():
    rec = StubRecorder()
    st = _monitor(rec, 99).check()
    assert st.status == FULL and rec.paused and rec.events == ["pause"]


def test_hysteresis_resume_below_resume_mark():
    rec = StubRecorder()
    m = _monitor(rec, 99)
    m.check()                    # -> full, pause
    assert rec.paused
    m._used_percent = lambda: (100, 96, 4, 96.0)  # 96: above resume(95) -> stay paused
    m.check()
    assert rec.paused
    m._used_percent = lambda: (100, 94, 6, 94.0)  # below resume -> resume
    m.check()
    assert not rec.paused and rec.events == ["pause", "resume"]


def test_failed_pause_is_retried_before_resuming_after_space_recovers(monkeypatch):
    rec = StubRecorder()
    monitor = _monitor(rec, 99)
    original = rec.pause
    failures = [True]

    def pause():
        if failures.pop():
            raise RuntimeError("synthetic unconfirmed exit")
        original()

    monkeypatch.setattr(rec, "pause", pause)
    with pytest.raises(RuntimeError):
        monitor.check()
    failures.append(False)
    monitor._used_percent = lambda: (100, 90, 10, 90.0)
    monitor.check()  # Finish the pending pause even though space recovered.
    assert rec.events == ["pause"] and rec.paused
    monitor.check()
    assert rec.events == ["pause", "resume"] and not rec.paused


def test_failed_resume_keeps_storage_intent_for_next_check(monkeypatch):
    rec = StubRecorder()
    monitor = _monitor(rec, 99)
    monitor.check()
    original = rec.resume

    def fail():
        raise RuntimeError("synthetic cleanup still pending")

    monkeypatch.setattr(rec, "resume", fail)
    monitor._used_percent = lambda: (100, 90, 10, 90.0)
    with pytest.raises(RuntimeError):
        monitor.check()
    assert monitor._paused_by_full and rec.paused
    monkeypatch.setattr(rec, "resume", original)
    monitor.check()
    assert not rec.paused and not monitor._paused_by_full
