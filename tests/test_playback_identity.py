"""Only synthetic files/processes: never encode or touch production recordings."""

from pathlib import Path
from types import SimpleNamespace

import pytest

from backend.app.recording import playback


def test_changed_source_invalidates_cache_without_deleting_original(tmp_path):
    source = tmp_path / "clip.mp4"
    source.write_bytes(b"source")
    cached = playback.cache_path(source)
    cached.write_bytes(b"derived")
    assert playback.cached_path(source) == cached
    source.write_bytes(b"longer source")
    assert playback.cached_path(source) is None
    assert playback.cache_path(source) != cached
    assert cached.exists() and source.read_bytes() == b"longer source"


@pytest.mark.parametrize("when", ["queued", "encoding"])
@pytest.mark.parametrize("change", ["grow", "replace", "remove"])
def test_changed_source_never_publishes_partial_or_stale_copy(tmp_path, monkeypatch, when, change):
    source = tmp_path / "clip.mp4"
    source.write_bytes(b"source")
    job = playback._TranscodeJob(source)
    calls = []

    def mutate():
        if change == "grow":
            source.write_bytes(b"growing-source")
        elif change == "replace":
            replacement = tmp_path / "new.mp4"
            replacement.write_bytes(b"source")
            replacement.replace(source)
        else:
            source.unlink()

    def encode(command, **kwargs):
        calls.append(True)
        Path(command[-1]).write_bytes(b"partial-conversion")
        mutate()
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(playback.subprocess, "run", encode)
    if when == "queued":
        mutate()
    playback._JOBS[job.segment] = job
    job._run()
    assert job.failed and job.done.is_set() and job.segment not in playback._JOBS
    assert not job.cache.exists() and not job.part.exists()
    assert bool(calls) == (when == "encoding")


def test_missing_source_and_empty_cache_are_not_hits(tmp_path):
    source = tmp_path / "clip.mp4"
    source.write_bytes(b"source")
    playback.cache_path(source).touch()
    assert playback.cached_path(source) is None
    source.unlink()
    playback.cache_path(source).write_bytes(b"orphan")
    assert playback.cached_path(source) is None


def test_empty_cache_can_be_rebuilt(tmp_path, monkeypatch):
    source = tmp_path / "clip.mp4"
    source.write_bytes(b"source")
    playback.cache_path(source).touch()
    def encode(command, **kwargs):
        Path(command[-1]).write_bytes(b"complete")
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(playback.subprocess, "run", encode)
    job = playback._TranscodeJob(source)
    job._run()
    assert not job.failed and playback.cached_path(source).read_bytes() == b"complete"
