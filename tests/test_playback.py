"""Recordings playback transcoding (HEVC -> browser-friendly H.264, streamed + cached)."""
from __future__ import annotations

import os
import time
from pathlib import Path

from backend.app import config
from backend.app.recording import playback


def test_video_codec_parses_ffprobe(monkeypatch):
    class _R:
        stdout = "hevc\n"
    monkeypatch.setattr(playback.subprocess, "run", lambda *a, **k: _R())
    assert playback.video_codec(Path("/x.mp4")) == "hevc"


def test_video_codec_empty_when_ffprobe_unavailable(monkeypatch):
    def boom(*a, **k):
        raise OSError("no ffprobe")
    monkeypatch.setattr(playback.subprocess, "run", boom)
    assert playback.video_codec(Path("/x.mp4")) == ""


def test_needs_transcode_only_for_non_browser_codecs(monkeypatch):
    monkeypatch.setattr(playback, "video_codec", lambda seg: "hevc")
    assert playback.needs_transcode(Path("/x.mp4")) is True          # HEVC -> must transcode
    monkeypatch.setattr(playback, "video_codec", lambda seg: "h264")
    assert playback.needs_transcode(Path("/x.mp4")) is False         # already browser-playable
    monkeypatch.setattr(playback, "video_codec", lambda seg: "")     # unknown -> don't blindly transcode
    assert playback.needs_transcode(Path("/x.mp4")) is False


def test_cache_path_deterministic_and_under_cache_root():
    seg = Path("/recordings/aa/2026-07-28/06/x.mp4")
    p1, p2 = playback.cache_path(seg), playback.cache_path(seg)
    assert p1 == p2                                                  # same segment -> same cache file
    assert p1.parent.name == "playback_cache" and p1.suffix == ".mp4"
    assert playback.cache_path(Path("/recordings/bb/y.mp4")) != p1   # different segment -> different file


def test_ffmpeg_cmd_transcodes_video_copies_audio_faststart():
    cmd = playback._ffmpeg_cmd(Path("/in.mp4"), Path("/out.mp4"))
    assert "libx264" in cmd and "copy" in cmd                        # H.264 video, AAC audio copied
    assert "+faststart" in " ".join(cmd)                            # seekable, real duration up front


def _fake_run(monkeypatch, *, rc=0, write=b"H264-MP4"):
    def run(cmd, **kw):
        if rc == 0:
            Path(cmd[-1]).write_bytes(write)                        # simulate ffmpeg writing output
        class _P:
            returncode = rc
        return _P()
    monkeypatch.setattr(playback.subprocess, "run", run)


def test_transcoded_path_transcodes_and_caches_hevc(monkeypatch, tmp_path):
    monkeypatch.setattr(playback, "needs_transcode", lambda seg: True)
    _fake_run(monkeypatch)
    seg = tmp_path / "seg.mp4"
    seg.write_bytes(b"hevc-source")
    out = playback.transcoded_path(seg)
    assert out is not None and out == playback.cache_path(seg)
    assert out.read_bytes() == b"H264-MP4"                          # produced the transcode
    assert not list(out.parent.glob("*.part"))                      # no leftover temp
    # second call is a cache hit — no re-transcode
    monkeypatch.setattr(playback.subprocess, "run",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("re-transcoded")))
    assert playback.transcoded_path(seg) == out


def test_transcoded_path_none_for_browser_playable(monkeypatch, tmp_path):
    monkeypatch.setattr(playback, "needs_transcode", lambda seg: False)   # already H.264
    seg = tmp_path / "seg.mp4"; seg.write_bytes(b"x")
    assert playback.transcoded_path(seg) is None                    # caller serves the original


def test_transcoded_path_none_and_no_cache_on_failure(monkeypatch, tmp_path):
    monkeypatch.setattr(playback, "needs_transcode", lambda seg: True)
    _fake_run(monkeypatch, rc=1)                                    # ffmpeg failed
    seg = tmp_path / "seg.mp4"; seg.write_bytes(b"hevc")
    assert playback.transcoded_path(seg) is None
    assert not playback.cache_path(seg).is_file() and not list(playback.cache_path(seg).parent.glob("*.part"))


def test_background_prepare_builds_faststart_cache_once(monkeypatch, tmp_path):
    seg = tmp_path / "seg.mp4"
    seg.write_bytes(b"hevc")
    calls = []

    def run(cmd, **kwargs):
        calls.append(cmd)
        output = Path(cmd[-1])
        output.write_bytes(b"faststart")

        class Result:
            returncode = 0
        return Result()

    monkeypatch.setattr(playback.subprocess, "run", run)
    monkeypatch.setattr(playback, "needs_transcode", lambda _segment: True)
    assert playback.prepare_transcode(seg) is True
    deadline = time.monotonic() + 2
    while playback.transcode_in_progress(seg) and time.monotonic() < deadline:
        time.sleep(0.01)

    cache = playback.cache_path(seg)
    assert cache.read_bytes() == b"faststart"
    assert len(calls) == 1 and "libx264" in calls[0] and "+faststart" in calls[0]
    assert playback.transcode_in_progress(seg) is False
    assert playback.prepare_transcode(seg) is False                 # completed cache is reused


def _set_cap(monkeypatch, mb):
    monkeypatch.setenv("PLAYBACK_CACHE_MB", str(mb))
    config.get_settings.cache_clear()


def _make_cache_file(name: str, size: int, mtime: float) -> Path:
    f = playback._cache_root() / name
    f.write_bytes(b"\0" * size)
    os.utime(f, (mtime, mtime))
    return f


def test_evict_deletes_lru_files_over_cap(monkeypatch):
    # cap = 5 MB; three 2 MB files, total 6 MB → drop only the oldest (4 MB now <= cap)
    _set_cap(monkeypatch, 5)
    old = _make_cache_file("aaaaaaaaaaaaaaaaaaaa.mp4", 2 * 1024 * 1024, mtime=100)
    mid = _make_cache_file("bbbbbbbbbbbbbbbbbbbb.mp4", 2 * 1024 * 1024, mtime=200)
    new = _make_cache_file("cccccccccccccccccccc.mp4", 2 * 1024 * 1024, mtime=300)
    playback._evict()
    assert not old.is_file()                                       # LRU victim
    assert mid.is_file() and new.is_file()                         # newest survive


def test_evict_never_deletes_keep(monkeypatch):
    _set_cap(monkeypatch, 1)
    old = _make_cache_file("aaaaaaaaaaaaaaaaaaaa.mp4", 2 * 1024 * 1024, mtime=100)
    playback._evict(keep=old)                                      # over cap, but it's the one we serve
    assert old.is_file()


def test_evict_disabled_when_cap_zero(monkeypatch):
    _set_cap(monkeypatch, 0)
    f = _make_cache_file("aaaaaaaaaaaaaaaaaaaa.mp4", 5 * 1024 * 1024, mtime=100)
    playback._evict()
    assert f.is_file()                                             # unbounded → nothing removed


# --- background pre-transcode warmer -----------------------------------------------

def test_warmer_disabled_is_noop():
    w = playback.Warmer(enabled=False)
    assert w.warm_once() is False
    w.start()
    assert w._thread is None                                       # no thread for a disabled warmer


def test_warmer_warms_newest_uncached_hevc(monkeypatch, tmp_path):
    from backend.app.recording import recorder
    seg = tmp_path / "seg.mp4"; seg.write_bytes(b"hevc")
    monkeypatch.setattr(recorder, "query_segments", lambda **k: {"items": [{"path": str(seg)}]})
    monkeypatch.setattr(playback, "needs_transcode", lambda s: True)
    warmed = []
    monkeypatch.setattr(playback, "transcoded_path", lambda s: warmed.append(s) or playback.cache_path(s))
    assert playback.Warmer(enabled=True).warm_once() is True
    assert warmed == [seg]                                         # transcoded the pending segment


def test_warmer_next_skips_cached_and_browser_playable(monkeypatch, tmp_path):
    from backend.app.recording import recorder
    cached = tmp_path / "a.mp4"; cached.write_bytes(b"x")
    playback.cache_path(cached).write_bytes(b"c")                 # already in the cache
    h264 = tmp_path / "b.mp4"; h264.write_bytes(b"x")            # doesn't need transcode
    monkeypatch.setattr(recorder, "query_segments",
                        lambda **k: {"items": [{"path": str(cached)}, {"path": str(h264)}]})
    monkeypatch.setattr(playback, "needs_transcode", lambda s: s != h264)
    assert playback.Warmer(enabled=True)._next_segment() is None  # nothing left to warm


def test_warmer_stops_before_cap(monkeypatch, tmp_path):
    from backend.app.recording import recorder
    seg = tmp_path / "seg.mp4"; seg.write_bytes(b"x")
    monkeypatch.setattr(recorder, "query_segments", lambda **k: {"items": [{"path": str(seg)}]})
    monkeypatch.setattr(playback, "needs_transcode", lambda s: True)
    monkeypatch.setattr(playback, "_cache_size", lambda: 95 * 1024 * 1024)   # 95 MB used
    _set_cap(monkeypatch, 100)                                     # cap 100 MB; headroom = 90 MB
    hit = []
    monkeypatch.setattr(playback, "transcoded_path", lambda s: hit.append(s))
    assert playback.Warmer(enabled=True).warm_once() is False     # over headroom → don't churn
    assert hit == []
