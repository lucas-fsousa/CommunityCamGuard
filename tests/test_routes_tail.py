"""A few remaining route/config branches to round out coverage."""
from pathlib import Path
from types import SimpleNamespace

from backend.app import config
from backend.app.api import routes
from backend.app.config import get_settings
from backend.app.db import registry


def test_list_cameras_marks_the_recording_flag():
    registry.init_db()
    registry.upsert_camera("aa:bb:cc:dd:ee:01", last_ip="10.0.0.5", stream_path="/onvif1")
    rec = SimpleNamespace(is_recording=lambda mac: True)
    req = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(rec=rec)))
    out = routes.list_cameras(req)
    assert len(out) == 1 and out[0]["recording"] is True


def test_recording_file_serves_an_existing_segment(monkeypatch):
    root = Path(get_settings().recordings_dir)
    seg = root / "aabbccddee01" / "2026-08-01" / "12" / "20260801_120000.mp4"
    seg.parent.mkdir(parents=True, exist_ok=True)
    seg.write_bytes(b"x" * 10)
    monkeypatch.setattr(routes.playback, "transcoded_path", lambda t: None)   # H.264 -> serve as-is
    resp = routes.recording_file(path=str(seg))
    assert resp.status_code == 200                     # a FileResponse for the segment


def test_scan_subnets_splits_and_trims(monkeypatch):
    monkeypatch.setenv("DISCOVERY_SCAN_SUBNETS", "192.168.1.0/24, 10.0.0.0/24 ,")
    config.get_settings.cache_clear()
    assert config.get_settings().scan_subnets == ["192.168.1.0/24", "10.0.0.0/24"]
    config.get_settings.cache_clear()
