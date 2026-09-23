from pathlib import Path

import pytest

from backend.app.recording import codec_cache, playback


@pytest.fixture(autouse=True)
def clean_cache():
    codec_cache._VALUES.clear()
    yield
    codec_cache._VALUES.clear()


def test_repeated_unchanged_file_probes_once(tmp_path):
    path = tmp_path / "clip.mp4"
    path.write_bytes(b"sample")
    calls = []
    def probe(value):
        calls.append(value)
        return "hevc"
    assert codec_cache.lookup(path, probe) == codec_cache.lookup(path, probe) == "hevc"
    assert calls == [path.resolve()]


def test_file_growth_and_atomic_replacement_invalidate_metadata(tmp_path):
    path = tmp_path / "clip.mp4"
    path.write_bytes(b"a")
    assert codec_cache.lookup(path, lambda _: "hevc") == "hevc"
    path.write_bytes(b"aa")
    assert codec_cache.lookup(path, lambda _: "h264") == "h264"
    other = tmp_path / "replacement.mp4"
    other.write_bytes(b"aa")
    other.replace(path)
    assert codec_cache.lookup(path, lambda _: "av1") == "av1"


def test_missing_unknown_and_changing_files_are_not_cached(tmp_path):
    missing = tmp_path / "missing.mp4"
    assert codec_cache.lookup(missing, lambda _: "") == ""
    path = tmp_path / "clip.mp4"
    path.write_bytes(b"a")
    assert codec_cache.lookup(path, lambda _: "") == ""
    def changing(value: Path):
        value.write_bytes(b"changed")
        return "hevc"
    assert codec_cache.lookup(path, changing) == "hevc"
    assert not codec_cache._VALUES
    assert codec_cache.lookup(path, lambda _: "h264") == "h264"


def test_lru_capacity_and_hits_refresh_recency(tmp_path, monkeypatch):
    monkeypatch.setattr(codec_cache, "_LIMIT", 2)
    files = [tmp_path / f"{i}.mp4" for i in range(3)]
    for path in files:
        path.write_bytes(b"a")
    for path in files[:2]:
        codec_cache.lookup(path, lambda _: "hevc")
    codec_cache.lookup(files[0], lambda _: pytest.fail("hit re-probed"))
    codec_cache.lookup(files[2], lambda _: "hevc")
    assert len(codec_cache._VALUES) == 2
    assert codec_cache.lookup(files[1], lambda _: "h264") == "h264"


def test_failed_ffprobe_output_is_not_published(tmp_path, monkeypatch):
    from types import SimpleNamespace
    path = tmp_path / "clip.mp4"
    path.write_bytes(b"a")
    monkeypatch.setattr(playback.subprocess, "run",
                        lambda *a, **k: SimpleNamespace(returncode=1, stdout="hevc\n"))
    assert playback.video_codec(path) == ""
    assert not codec_cache._VALUES
