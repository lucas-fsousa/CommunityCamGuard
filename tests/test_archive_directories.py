"""Local folders only: no source, account, FFmpeg or background thread."""

from datetime import UTC, datetime
from pathlib import Path

import pytest

from backend.app.media import archive_directories as module


def fixed_utc(monkeypatch, year, month, day, hour):
    class FixedDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            assert tz is UTC
            return cls(year, month, day, hour, 59, tzinfo=UTC)
    monkeypatch.setattr(module, "datetime", FixedDateTime)


def test_prepare_covers_midnight_and_year_change_without_touching_archives(tmp_path, monkeypatch):
    fixed_utc(monkeypatch, 2026, 12, 31, 23)
    template = tmp_path / "%Y-%m-%d/%H/%Y%m%d_%H%M%S.mp4"
    existing = tmp_path / "2026-12-31/23/existing.mp4"
    existing.parent.mkdir(parents=True)
    existing.write_bytes(b"preserve")
    module.ArchiveDirectories(template).prepare(force=True)
    assert existing.read_bytes() == b"preserve"
    assert (tmp_path / "2026-12-31/23").is_dir()
    assert (tmp_path / "2027-01-01/00").is_dir()
    assert (tmp_path / "2027-01-01/23").is_dir()
    assert len(list(tmp_path.glob("*/*"))) == 25


def test_refresh_is_bounded_and_extends_horizon(tmp_path, monkeypatch):
    ticks = [100.0]
    fixed_utc(monkeypatch, 2026, 10, 28, 23)
    directories = module.ArchiveDirectories(tmp_path / "%Y-%m-%d/%H/%Y%m%d_%H%M%S.mp4",
                                            clock=lambda: ticks[0])
    directories.prepare()
    future = tmp_path / "2026-10-30/00"
    fixed_utc(monkeypatch, 2026, 10, 29, 0)
    ticks[0] = 129.9
    directories.prepare()
    assert not future.exists()
    ticks[0] = 130
    directories.prepare()
    assert future.is_dir()


def test_forced_epoch_refresh_does_not_wait_for_interval(tmp_path, monkeypatch):
    fixed_utc(monkeypatch, 2026, 10, 28, 1)
    directories = module.ArchiveDirectories(tmp_path / "%Y-%m-%d/%H/%Y%m%d_%H%M%S.mp4", clock=lambda: 1)
    directories.prepare()
    fixed_utc(monkeypatch, 2026, 11, 1, 1)
    directories.prepare(force=True)
    assert (tmp_path / "2026-11-01/01").is_dir()


def test_failed_preparation_is_not_cached_as_ready(tmp_path, monkeypatch):
    directories = module.ArchiveDirectories(tmp_path / "%Y%m%d_%H%M%S.mp4", clock=lambda: 1)
    mkdir = Path.mkdir
    calls = []
    def fail_once(path, **kwargs):
        calls.append(path)
        if len(calls) == 1:
            raise PermissionError("private filesystem detail")
        return mkdir(path, **kwargs)
    monkeypatch.setattr(Path, "mkdir", fail_once)
    with pytest.raises(PermissionError):
        directories.prepare()
    directories.prepare()
    assert len(calls) == 2  # fixed directory is deduplicated across all 25 hours


def test_construction_has_no_filesystem_effect_and_rejects_constant_names(tmp_path):
    destination = tmp_path / "not-created"
    module.ArchiveDirectories(destination / "%Y%m%d_%H%M%S.mp4")
    assert not destination.exists()
    with pytest.raises(ValueError, match="timestamped UTC"):
        module.ArchiveDirectories(destination / "constant.mp4")
