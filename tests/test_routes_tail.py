"""A few remaining route/config branches to round out coverage."""

from pathlib import Path
from types import SimpleNamespace

from backend.app import config
from backend.app.api import recordings as recording_routes
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
    assert out[0]["online"] is False


def test_camera_status_uses_base_stream_packet_liveness():
    registry.init_db()
    cam = registry.upsert_camera("aa:bb:cc:dd:ee:01", last_ip="10.0.0.5", stream_path="/onvif1")
    media = SimpleNamespace(stream_online=lambda: {routes.go2rtc.stream_id(cam.camera_id): True})
    rec = SimpleNamespace(is_recording=lambda _mac: True)
    req = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(media=media, rec=rec)))

    assert routes.camera_statuses(req) == [
        {"id": cam.camera_id, "mac": cam.mac, "online": True, "recording": True}
    ]


def test_recording_file_serves_an_existing_segment(monkeypatch):
    root = Path(get_settings().recordings_dir)
    seg = root / "aabbccddee01" / "2026-08-01" / "12" / "20260801_120000.mp4"
    seg.parent.mkdir(parents=True, exist_ok=True)
    seg.write_bytes(b"x" * 10)
    monkeypatch.setattr(recording_routes.playback, "cached_path", lambda t: None)
    monkeypatch.setattr(recording_routes.playback, "needs_transcode", lambda t: False)
    resp = recording_routes.recording_file(path=str(seg))
    assert resp.status_code == 200  # a FileResponse for the segment


def test_recording_file_refuses_partial_hevc_and_starts_preparation(monkeypatch):
    root = Path(get_settings().recordings_dir)
    seg = root / "aabbccddee01" / "2026-08-01" / "12" / "20260801_120000.mp4"
    seg.parent.mkdir(parents=True, exist_ok=True)
    seg.write_bytes(b"hevc")
    monkeypatch.setattr(recording_routes.playback, "cached_path", lambda t: None)
    monkeypatch.setattr(recording_routes.playback, "needs_transcode", lambda t: True)
    started = []
    monkeypatch.setattr(
        recording_routes.playback, "prepare_transcode", lambda t: started.append(t) or True
    )

    try:
        recording_routes.recording_file(path=str(seg))
    except recording_routes.HTTPException as exc:
        assert exc.status_code == 409
    else:
        raise AssertionError("uncached HEVC was served before it became seekable")

    assert started == [seg.resolve()]


def test_prepare_recording_starts_shared_job_and_reports_progress(monkeypatch):
    root = Path(get_settings().recordings_dir)
    seg = root / "aabbccddee01" / "2026-08-01" / "12" / "20260801_120000.mp4"
    seg.parent.mkdir(parents=True, exist_ok=True)
    seg.write_bytes(b"hevc")
    monkeypatch.setattr(recording_routes.playback, "cached_path", lambda t: None)
    monkeypatch.setattr(recording_routes.playback, "needs_transcode", lambda t: True)
    running = {"value": False}
    monkeypatch.setattr(
        recording_routes.playback, "transcode_in_progress", lambda t: running["value"]
    )
    monkeypatch.setattr(
        recording_routes.playback,
        "prepare_transcode",
        lambda t: running.update(value=True) or True,
    )

    assert recording_routes.prepare_recording_playback(path=str(seg)) == {
        "ready": False,
        "cached": False,
        "transcoding": True,
    }


def test_recording_playback_status_reports_seekable_cache(monkeypatch):
    root = Path(get_settings().recordings_dir)
    seg = root / "aabbccddee01" / "2026-08-01" / "12" / "20260801_120000.mp4"
    cache = root / "cache.mp4"
    seg.parent.mkdir(parents=True, exist_ok=True)
    seg.write_bytes(b"hevc")
    cache.write_bytes(b"h264")
    monkeypatch.setattr(recording_routes.playback, "cached_path", lambda t: cache)

    assert recording_routes.recording_playback_status(path=str(seg)) == {
        "ready": True,
        "cached": True,
        "transcoding": False,
    }


def test_recording_download_uses_camera_name_and_original_timestamp():
    root = Path(get_settings().recordings_dir)
    seg = root / "aabbccddee01" / "2026-08-17" / "03" / "20260817_031500.mp4"
    seg.parent.mkdir(parents=True, exist_ok=True)
    seg.write_bytes(b"original recording")
    registry.init_db()
    registry.upsert_camera("aa:bb:cc:dd:ee:01", name="Garagem / Sul")

    resp = recording_routes.recording_download(path=str(seg))

    assert resp.status_code == 200
    assert Path(resp.path) == seg.resolve()
    assert 'filename="Garagem_Sul_20260817_031500.mp4"' in resp.headers["content-disposition"]
    assert resp.headers["cache-control"] == "private, no-store"


def test_recording_download_rejects_path_outside_recordings_root():
    try:
        recording_routes.recording_download(path="/etc/passwd")
    except recording_routes.HTTPException as exc:
        assert exc.status_code == 404
    else:
        raise AssertionError("outside recording path was accepted")


def test_scan_subnets_splits_and_trims(monkeypatch):
    monkeypatch.setenv("DISCOVERY_SCAN_SUBNETS", "192.168.1.0/24, 10.0.0.0/24 ,")
    config.get_settings.cache_clear()
    assert config.get_settings().scan_subnets == ["192.168.1.0/24", "10.0.0.0/24"]
    config.get_settings.cache_clear()
