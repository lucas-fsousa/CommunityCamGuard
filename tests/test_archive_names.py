"""Camera-independent UTC names: preserve legacy paths, isolate decoder epochs."""

from datetime import UTC, datetime
from pathlib import Path

import pytest

from backend.app.media.archive_names import epoch_template, started_at


def test_epochs_do_not_collide_when_started_in_the_same_wallclock_second(tmp_path):
    template = tmp_path / "%Y-%m-%d/%H/%Y%m%d_%H%M%S.mp4"
    names = [epoch_template(template) for _ in range(100)]
    assert len(set(names)) == 100
    assert all(name.parent == template.parent for name in names)
    for name in names:
        concrete = Path(datetime(2026, 10, 28, 23, 59, 59, tzinfo=UTC).strftime(str(name)))
        assert started_at(concrete) == datetime(2026, 10, 28, 23, 59, 59, tzinfo=UTC)


@pytest.mark.parametrize("name", ["20261028_235959.mp4", "20261028_235959_e" + "a" * 32 + ".mp4"])
def test_both_layouts_preserve_utc(name):
    assert started_at(Path(name)) == datetime(2026, 10, 28, 23, 59, 59, tzinfo=UTC)


@pytest.mark.parametrize("name", [
    "20260230_120000.mp4", "20261028_250000.mp4", "20261028_235959_unknown.mp4",
    "20261028_235959_e123.mp4", "20261028_235959_e" + "g" * 32 + ".mp4",
    "20261028_235959.mp4.bak", "prefix20261028_235959.mp4",
])
def test_malformed_names_are_not_silently_indexed(name):
    with pytest.raises(ValueError):
        started_at(Path(name))


def test_constant_or_already_expanded_templates_cannot_overwrite_one_filename():
    with pytest.raises(ValueError, match="UTC archive template"):
        epoch_template(Path("archive.mp4"))
