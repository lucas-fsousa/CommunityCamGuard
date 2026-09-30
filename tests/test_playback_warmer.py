"""Cover the playback Warmer's thread lifecycle + a couple of transcoded_path edges the main
test_playback.py leaves out."""
import os
import threading

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


def test_warmer_rebuilds_empty_cache_without_refreshing_valid_entries(monkeypatch, tmp_path):
    from backend.app.recording import recorder

    valid, empty = tmp_path / "valid.mp4", tmp_path / "empty.mp4"
    for source in (valid, empty):
        source.write_bytes(b"source")
    cached = playback.cache_path(valid)
    cached.write_bytes(b"derived")
    os.utime(cached, ns=(1_000_000_000, 1_000_000_000))
    original_mtime = cached.stat().st_mtime_ns
    playback.cache_path(empty).write_bytes(b"")
    monkeypatch.setattr(recorder, "query_segments", lambda **kw: {
        "items": [{"path": str(valid)}, {"path": str(empty)}],
    })
    monkeypatch.setattr(playback, "needs_transcode", lambda _: True)
    assert Warmer(enabled=True)._next_segment() == empty
    assert cached.stat().st_mtime_ns == original_mtime
