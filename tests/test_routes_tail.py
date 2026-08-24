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
    monkeypatch.setattr(routes.playback, "cached_path", lambda t: None)
    monkeypatch.setattr(routes.playback, "needs_transcode", lambda t: False)  # H.264 -> as-is
    resp = routes.recording_file(path=str(seg))
    assert resp.status_code == 200                     # a FileResponse for the segment


def test_recording_file_streams_first_hevc_view_and_reports_progress(monkeypatch):
    root = Path(get_settings().recordings_dir)
    seg = root / "aabbccddee01" / "2026-08-01" / "12" / "20260801_120000.mp4"
    seg.parent.mkdir(parents=True, exist_ok=True)
    seg.write_bytes(b"hevc")
    monkeypatch.setattr(routes.playback, "cached_path", lambda t: None)
    monkeypatch.setattr(routes.playback, "needs_transcode", lambda t: True)
    monkeypatch.setattr(routes.playback, "streaming_transcode", lambda t: iter([b"fragment"]))
    monkeypatch.setattr(routes.playback, "transcode_in_progress", lambda t: True)

    resp = routes.recording_file(path=str(seg))

    assert resp.status_code == 200
    assert resp.headers["x-playback-mode"] == "progressive"
    assert resp.headers["accept-ranges"] == "none"
    assert routes.recording_playback_status(path=str(seg)) == {
        "ready": False, "cached": False, "transcoding": True,
    }


def test_recording_playback_status_reports_seekable_cache(monkeypatch):
    root = Path(get_settings().recordings_dir)
    seg = root / "aabbccddee01" / "2026-08-01" / "12" / "20260801_120000.mp4"
    cache = root / "cache.mp4"
    seg.parent.mkdir(parents=True, exist_ok=True)
    seg.write_bytes(b"hevc")
    cache.write_bytes(b"h264")
    monkeypatch.setattr(routes.playback, "cached_path", lambda t: cache)

    assert routes.recording_playback_status(path=str(seg)) == {
        "ready": True, "cached": True, "transcoding": False,
    }


def test_recording_download_uses_camera_name_and_original_timestamp():
    root = Path(get_settings().recordings_dir)
    seg = root / "aabbccddee01" / "2026-08-17" / "03" / "20260817_031500.mp4"
    seg.parent.mkdir(parents=True, exist_ok=True)
    seg.write_bytes(b"original recording")
    registry.init_db()
    registry.upsert_camera("aa:bb:cc:dd:ee:01", name="Garagem / Sul")

    resp = routes.recording_download(path=str(seg))

    assert resp.status_code == 200
    assert Path(resp.path) == seg.resolve()
    assert 'filename="Garagem_Sul_20260817_031500.mp4"' in resp.headers["content-disposition"]
    assert resp.headers["cache-control"] == "private, no-store"


def test_recording_download_rejects_path_outside_recordings_root():
    try:
        routes.recording_download(path="/etc/passwd")
    except routes.HTTPException as exc:
        assert exc.status_code == 404
    else:
        raise AssertionError("outside recording path was accepted")


def test_scan_subnets_splits_and_trims(monkeypatch):
    monkeypatch.setenv("DISCOVERY_SCAN_SUBNETS", "192.168.1.0/24, 10.0.0.0/24 ,")
    config.get_settings.cache_clear()
    assert config.get_settings().scan_subnets == ["192.168.1.0/24", "10.0.0.0/24"]
    config.get_settings.cache_clear()
