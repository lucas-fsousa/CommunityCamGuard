import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

import pytest

from backend.app.recording import codec_cache, playback
from backend.app.recording.playback_budget import PlaybackBusy


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
    path = tmp_path / "clip.mp4"
    path.write_bytes(b"a")
    monkeypatch.setattr(playback.subprocess, "run",
                        lambda *a, **k: SimpleNamespace(returncode=1, stdout="hevc\n"))
    assert playback.video_codec(path) == ""
    assert not codec_cache._VALUES


def test_concurrent_same_file_misses_share_successful_probe(tmp_path):
    path = tmp_path / "clip.mp4"
    path.write_bytes(b"sample")
    started, release = threading.Event(), threading.Event()
    calls = []

    def probe(value):
        calls.append(value)
        started.set()
        assert release.wait(2)
        return "hevc"

    with ThreadPoolExecutor(max_workers=4) as pool:
        first = pool.submit(codec_cache.lookup, path, probe)
        try:
            assert started.wait(2)
            others = [pool.submit(codec_cache.lookup, path, probe) for _ in range(3)]
        finally:
            release.set()
        assert first.result(timeout=2) == "hevc"
        assert [future.result(timeout=2) for future in others] == ["hevc"] * 3
    assert calls == [path.resolve()]


def test_distinct_files_have_bounded_parallel_probes(tmp_path):
    # Select different stripes deterministically for this interpreter's hash seed.
    paths = {}
    for index in range(10000):
        path = tmp_path / f"{index}.mp4"
        paths.setdefault(hash(str(path)) % len(codec_cache._STRIPES), path)
        if len(paths) == 4:
            break
    assert len(paths) == 4
    for path in paths.values():
        path.write_bytes(b"sample")
    lock = threading.Lock()
    two_started, release = threading.Event(), threading.Event()
    active = peak = 0

    def probe(_):
        nonlocal active, peak
        with lock:
            active += 1
            peak = max(peak, active)
            if active == 2:
                two_started.set()
        try:
            assert release.wait(2)
            return "h264"
        finally:
            with lock:
                active -= 1

    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = [pool.submit(codec_cache.lookup, path, probe) for path in paths.values()]
        try:
            assert two_started.wait(2)
        finally:
            release.set()
        assert [future.result(timeout=2) for future in futures] == ["h264"] * 4
    assert peak == 2


def test_probe_exception_releases_admission_and_does_not_cache(tmp_path):
    path = tmp_path / "clip.mp4"
    path.write_bytes(b"sample")

    def broken(_):
        raise RuntimeError("probe failed")

    for _ in range(3):
        with pytest.raises(RuntimeError, match="probe failed"):
            codec_cache.lookup(path, broken)
    assert not codec_cache._VALUES
    assert codec_cache.lookup(path, lambda _: "h264") == "h264"


@pytest.mark.parametrize("resource", ["stripe", "probes"])
def test_saturated_admission_is_bounded_and_recoverable(tmp_path, monkeypatch, resource):
    path = tmp_path / "clip.mp4"
    path.write_bytes(b"sample")
    monkeypatch.setattr(codec_cache, "ADMISSION_SECONDS", 0.01)
    held = (codec_cache._STRIPES[hash(str(path.resolve())) % len(codec_cache._STRIPES)]
            if resource == "stripe" else threading.BoundedSemaphore(1))
    if resource == "probes":
        monkeypatch.setattr(codec_cache, "_PROBES", held)
    held.acquire()
    try:
        with pytest.raises(PlaybackBusy):
            codec_cache.lookup(path, lambda _: pytest.fail("saturated probe ran"))
        assert not codec_cache._VALUES
    finally:
        held.release()
    assert codec_cache.lookup(path, lambda _: "hevc") == "hevc"


def test_stripe_and_probe_slot_share_one_deadline(tmp_path, monkeypatch):
    path = tmp_path / "clip.mp4"
    path.write_bytes(b"sample")
    clock = iter([100.0, 100.75])
    monkeypatch.setattr(codec_cache, "time", SimpleNamespace(monotonic=lambda: next(clock)))
    timeouts = []

    class Saturated:
        def acquire(self, *, timeout):
            timeouts.append(timeout)
            return False

    monkeypatch.setattr(codec_cache, "_PROBES", Saturated())
    with pytest.raises(PlaybackBusy):
        codec_cache.lookup(path, lambda _: pytest.fail("probe ran without admission"))
    assert timeouts == [0.25]
