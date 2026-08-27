"""Time-based recording retention — the sporadic cleanup job."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

from backend.app import config
from backend.app.db import connect
from backend.app.recording import recorder, retention


def _seed_segment(root, *, started: datetime, mac="aabbccddeeff") -> object:
    """Create a segment file laid out by day/hour and its index row; return the file path."""
    day, hour = started.strftime("%Y-%m-%d"), started.strftime("%H")
    d = root / mac / day / hour
    d.mkdir(parents=True, exist_ok=True)
    f = d / (started.strftime("%Y%m%d_%H%M%S") + ".mp4")
    f.write_bytes(b"segment")
    recorder.init_db()
    with connect() as c:
        c.execute(
            "INSERT INTO recordings (mac,path,started_at,day,hour,size_bytes,duration_s,indexed_at)"
            " VALUES (?,?,?,?,?,?,?,?)",
            (mac, str(f), started.isoformat(timespec="seconds"), day, int(hour), 7, 60, "x"),
        )
    return f


def _count_rows() -> int:
    with connect() as c:
        return c.execute("SELECT COUNT(*) FROM recordings").fetchone()[0]


def test_purge_deletes_expired_keeps_recent(tmp_path):
    recent = _seed_segment(tmp_path, started=datetime.now(UTC) - timedelta(days=2))
    old = _seed_segment(tmp_path, started=datetime.now(UTC) - timedelta(days=10))
    cleaner = retention.RetentionCleaner(retention_days=7)
    cleaner.root = tmp_path
    removed = cleaner.purge()
    assert removed == 1
    assert recent.is_file() and not old.is_file()          # only the expired file is gone
    assert _count_rows() == 1                               # its index row went with it


def test_purge_prunes_empty_dirs(tmp_path):
    old = _seed_segment(tmp_path, started=datetime.now(UTC) - timedelta(days=30))
    day_dir = old.parent.parent
    cleaner = retention.RetentionCleaner(retention_days=7)
    cleaner.root = tmp_path
    cleaner.purge()
    assert not old.parent.exists() and not day_dir.exists()  # empty hour + day dirs removed


def test_prune_preserves_current_and_future_recorder_directories(tmp_path):
    cleaner = retention.RetentionCleaner(retention_days=7)
    cleaner.root = tmp_path
    now = datetime.now(UTC).replace(minute=0, second=0, microsecond=0)
    mac_root = tmp_path / "aabbccddeeff"

    def hour_dir(when):
        path = mac_root / when.strftime("%Y-%m-%d") / when.strftime("%H")
        path.mkdir(parents=True, exist_ok=True)
        return path

    old = hour_dir(now - timedelta(days=2))
    current = hour_dir(now)
    future = hour_dir(now + timedelta(hours=24))

    cleaner._prune_empty_dirs()

    assert not old.exists()
    assert current.is_dir() and future.is_dir()


def test_purge_also_drops_playback_cache(tmp_path, monkeypatch):
    from backend.app.recording import playback
    old = _seed_segment(tmp_path, started=datetime.now(UTC) - timedelta(days=10))
    cache = playback.cache_path(old)
    cache.write_bytes(b"transcoded")                        # pretend it was viewed & cached
    cleaner = retention.RetentionCleaner(retention_days=7)
    cleaner.root = tmp_path
    cleaner.purge()
    assert not cache.is_file()                              # derived transcode purged too


def test_retention_zero_keeps_forever(tmp_path):
    old = _seed_segment(tmp_path, started=datetime.now(UTC) - timedelta(days=999))
    cleaner = retention.RetentionCleaner(retention_days=0)  # disabled
    cleaner.root = tmp_path
    assert cleaner.purge() == 0
    assert old.is_file() and _count_rows() == 1


def test_retention_days_floored_and_default(monkeypatch):
    monkeypatch.setenv("RECORDING_RETENTION_DAYS", "-5")
    config.get_settings.cache_clear()
    assert config.get_settings().recording_retention_days == 0   # floored to 0
    monkeypatch.setenv("RECORDING_RETENTION_DAYS", "7")
    config.get_settings.cache_clear()
    assert config.get_settings().recording_retention_days == 7


def test_start_noop_when_disabled(tmp_path):
    cleaner = retention.RetentionCleaner(retention_days=0)
    cleaner.start()
    assert cleaner._thread is None                          # no thread spun for a disabled job


def test_retention_cutoff_is_explicit_utc():
    cleaner = retention.RetentionCleaner(retention_days=7)
    cutoff = cleaner._cutoff_iso()
    assert cutoff.endswith("+00:00")
    assert datetime.fromisoformat(cutoff).tzinfo is UTC
