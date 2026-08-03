"""Cover the background-thread lifecycles of the storage monitor and retention cleaner (start /
_loop / stop) plus StorageMonitor._used_percent — the parts the logic-only tests skip. The loop
body is stubbed with an Event so these stay fast and deterministic (no sleeps)."""
import threading

from backend.app.recording.retention import RetentionCleaner
from backend.app.recording.storage import StorageMonitor


def test_used_percent_reads_the_real_filesystem(tmp_path):
    total, used, free, pct = StorageMonitor(path=tmp_path)._used_percent()
    assert total > 0 and used >= 0 and free >= 0
    assert 0.0 <= pct <= 100.0
    assert tmp_path.exists()          # ensured the directory


def test_storage_monitor_loop_runs_check_then_stops(monkeypatch):
    m = StorageMonitor(interval=10)   # long interval: one check, then it waits, then we stop
    ran = threading.Event()
    monkeypatch.setattr(m, "check", ran.set)
    m.start()
    m.start()                         # idempotent: a second start is a no-op while alive
    try:
        assert ran.wait(2) is True    # the loop invoked check()
    finally:
        m.stop()
    assert not m._thread.is_alive()


def test_retention_cleaner_loop_runs_purge_then_stops(monkeypatch):
    c = RetentionCleaner(retention_days=7, interval=10)
    ran = threading.Event()
    monkeypatch.setattr(c, "purge", ran.set)
    c.start()
    try:
        assert ran.wait(2) is True
    finally:
        c.stop()
    assert not c._thread.is_alive()
