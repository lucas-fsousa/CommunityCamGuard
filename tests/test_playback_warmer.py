"""Cover the playback Warmer's thread lifecycle + a couple of transcoded_path edges the main
test_playback.py leaves out."""
import threading
from pathlib import Path

from backend.app.recording import playback
from backend.app.recording.playback import Warmer


def test_warmer_start_stop_runs_the_loop(monkeypatch):
    w = Warmer(enabled=True, interval=10)
    ran = threading.Event()
    monkeypatch.setattr(w, "warm_once", lambda: ran.set() or False)
    w.start()
    w.start()                          # idempotent while alive
    try:
        assert ran.wait(2) is True     # the loop invoked warm_once
    finally:
        w.stop()
    assert not w._thread.is_alive()


def test_warmer_start_is_noop_when_disabled():
    w = Warmer(enabled=False)
    w.start()
    assert w._thread is None           # no thread spun up
    w.stop()                           # safe even without a thread


def test_warm_once_false_when_nothing_pending(monkeypatch):
    w = Warmer(enabled=True)
    monkeypatch.setattr(w, "_next_segment", lambda: None)
    assert w.warm_once() is False


def test_transcoded_path_swallows_a_transcode_exception(monkeypatch, tmp_path):
    monkeypatch.setattr(playback, "needs_transcode", lambda seg: True)
    def boom(cmd, **kw):
        raise OSError("ffmpeg missing")
    monkeypatch.setattr(playback.subprocess, "run", boom)
    seg = tmp_path / "seg.mp4"
    seg.write_bytes(b"hevc")
    assert playback.transcoded_path(seg) is None            # no crash
    assert not playback.cache_path(seg).is_file()
    assert not list(playback.cache_path(seg).parent.glob("*.part"))   # temp cleaned up
