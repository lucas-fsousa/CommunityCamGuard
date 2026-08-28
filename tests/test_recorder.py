"""Tests for the recorder (backend/app/recording/recorder.py) beyond the supervisor: the ffmpeg
command build, segment indexing, and the start/pause/resume/stop lifecycle. subprocess is faked,
so no real ffmpeg runs.
"""
from datetime import UTC, datetime

import pytest

from backend.app.camera_identity import stable_camera_id
from backend.app.db import registry
from backend.app.db.registry import Camera
from backend.app.recording import recorder
from backend.app.recording.recorder import Recorder


class FakeProc:
    """Stand-in for subprocess.Popen — reports running until terminated/killed."""
    def __init__(self):
        self.pid = 999_999
        self._alive = True
        self.terminated = False
        self.killed = False

    def poll(self):
        return None if self._alive else 0

    def terminate(self):
        self._alive = False
        self.terminated = True

    def wait(self, timeout=None):
        return 0

    def kill(self):
        self.killed = True


# --- _spawn: the ffmpeg command -----------------------------------------------------

def test_spawn_builds_a_fragmented_mp4_segment_command(monkeypatch):
    captured = {}

    def fake_popen(cmd, **kw):
        captured["cmd"] = cmd
        captured["env"] = kw["env"]
        return FakeProc()

    monkeypatch.setattr(recorder.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(
        recorder.go2rtc, "restream_rtsp_url", lambda camera_id: "rtsp://127.0.0.1:3203/cam_x"
    )
    rec = Recorder(segment_seconds=300)
    camera_id = stable_camera_id("mac", "aa:bb:cc:dd:ee:01")
    rec._cameras[camera_id] = Camera(mac="aa:bb:cc:dd:ee:01", camera_id=camera_id)
    rec._spawn(camera_id)
    cmd = captured["cmd"]
    assert cmd[0] == "ffmpeg"
    assert "-c:v" in cmd and cmd[cmd.index("-c:v") + 1] == "copy"          # remux, no re-encode
    assert cmd[cmd.index("-i") + 1] == "rtsp://127.0.0.1:3203/cam_x"       # the go2rtc restream
    assert "1" == cmd[cmd.index("-use_wallclock_as_timestamps") + 1]      # timestamp repair
    assert cmd[cmd.index("-segment_time") + 1] == "300"                    # from segment_seconds
    # crash-safe fragmented MP4 (ADR 0004)
    assert "movflags=+frag_keyframe+empty_moov+default_base_moof" in cmd
    assert cmd[-1].endswith("%Y%m%d_%H%M%S.mp4")                           # strftime output template
    assert f"/{camera_id}/" in cmd[-1]
    assert captured["env"]["TZ"] == "UTC0"                               # filenames are always UTC


def test_ensure_dirs_uses_utc_across_hour_and_day_rollover(monkeypatch):
    class FixedDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            assert tz is UTC
            return cls(2026, 8, 31, 23, 30, tzinfo=UTC)

    monkeypatch.setattr(recorder, "datetime", FixedDateTime)
    rec = Recorder(segment_seconds=300)
    camera_id = stable_camera_id("mac", "aa:bb:cc:dd:ee:01")
    rec._ensure_dirs([camera_id])
    root = rec.root / camera_id
    assert (root / "2026-08-31" / "23").is_dir()
    assert (root / "2026-09-01" / "00").is_dir()
    assert (root / "2026-09-01" / "23").is_dir()          # full-day outage cushion
    assert len(list(root.glob("*/*"))) == recorder._DIR_LOOKAHEAD_HOURS + 1


def test_recording_output_rejects_native_or_path_like_identifiers():
    rec = Recorder(segment_seconds=300)
    for unsafe in ("aa:bb:cc:dd:ee:01", "../../outside", "cam_not_valid"):
        with pytest.raises(ValueError, match="opaque camera_id"):
            rec._cam_dir(unsafe)


def test_maintenance_survives_a_transient_index_failure(monkeypatch, caplog):
    rec = Recorder(segment_seconds=300, maint_interval=0.001)
    rec._cameras = {}
    calls = {"index": 0}

    def flaky_index():
        calls["index"] += 1
        if calls["index"] == 1:
            raise RuntimeError("temporary database lock")
        rec._stop.set()
        return 0

    monkeypatch.setattr(rec, "_index", flaky_index)
    rec._maintain()

    assert calls["index"] == 2
    assert "recorder maintenance indexing pass failed" in caplog.text


# --- _index: pick up finalized segments ---------------------------------------------

def _make_segment(rec, directory_key, when="20260801_120000"):
    started = datetime.strptime(when, "%Y%m%d_%H%M%S")
    key = directory_key if directory_key.startswith("cam_") else recorder._safe_mac(directory_key)
    d = rec.root / key / started.strftime("%Y-%m-%d") / started.strftime("%H")
    d.mkdir(parents=True, exist_ok=True)
    seg = d / f"{when}.mp4"
    seg.write_bytes(b"x" * 100)
    return seg


def test_index_records_finalized_segments():
    recorder.init_db()
    rec = Recorder(segment_seconds=300)
    seg = _make_segment(rec, "aa:bb:cc:dd:ee:01")
    added = rec._index(list_all=True)
    assert added == 1
    res = recorder.query_segments()
    assert res["total"] == 1 and res["items"][0]["path"] == str(seg)
    assert res["items"][0]["started_at"] == "2026-08-01T12:00:00+00:00"
    assert res["items"][0]["day"] == "2026-08-01"
    assert res["items"][0]["hour"] == 12


def test_index_records_opaque_archive_owner_and_compatibility_mac():
    registry.init_db()
    camera = registry.upsert_camera("aa:bb:cc:dd:ee:01")
    recorder.init_db()
    rec = Recorder(segment_seconds=300)
    seg = _make_segment(rec, camera.camera_id)

    assert rec._index(list_all=True) == 1
    item = recorder.query_segments(camera_id=camera.camera_id)["items"][0]
    assert item["path"] == str(seg)
    assert item["camera_id"] == camera.camera_id
    assert item["mac"] == "aabbccddee01"


def test_index_skips_unparseable_filenames():
    recorder.init_db()
    rec = Recorder(segment_seconds=300)
    d = rec.root / recorder._safe_mac("aa:bb:cc:dd:ee:01") / "2026-08-01" / "12"
    d.mkdir(parents=True, exist_ok=True)
    (d / "notatimestamp.mp4").write_bytes(b"x")
    assert rec._index(list_all=True) == 0


def test_index_is_idempotent_for_unchanged_segments():
    recorder.init_db()
    rec = Recorder(segment_seconds=300)
    _make_segment(rec, "aa:bb:cc:dd:ee:01")
    assert rec._index(list_all=True) == 1
    assert rec._index(list_all=True) == 0     # already indexed, same size -> no churn


# --- lifecycle: start / is_recording / pause / resume / stop ------------------------

def test_lifecycle_start_pause_resume_stop(monkeypatch):
    monkeypatch.setattr(recorder.subprocess, "Popen", lambda cmd, **kw: FakeProc())
    monkeypatch.setattr(
        recorder.go2rtc, "restream_rtsp_url", lambda camera_id: "rtsp://127.0.0.1:3203/cam_x"
    )
    rec = Recorder(segment_seconds=300, maint_interval=30)   # slow loop: won't interfere
    camera_id = stable_camera_id("mac", "aa:bb:cc:dd:ee:01")
    cam = Camera(
        mac="aa:bb:cc:dd:ee:01", camera_id=camera_id,
        last_ip="10.0.0.5", stream_path="/onvif1",
    )
    try:
        camera_ids = rec.start(cameras=[cam])
        assert camera_ids == [camera_id]
        assert rec.is_recording(camera_id) is True
        assert rec.paused is False

        rec.pause()
        assert rec.paused is True
        assert rec.is_recording(camera_id) is False   # process terminated
        rec.resume()
        assert rec.paused is False
    finally:
        rec.stop()   # joins the maintenance thread, terminates procs, final index


def test_same_public_id_keeps_recorder_when_native_mac_changes(monkeypatch):
    """Neither source nor archive path changes when only a driver-native identifier changes."""
    spawned = []
    monkeypatch.setattr(recorder.subprocess, "Popen", lambda cmd, **kw: FakeProc())
    rec = Recorder(segment_seconds=300, maint_interval=30)
    monkeypatch.setattr(rec, "_ensure_dirs", lambda macs: None)
    monkeypatch.setattr(rec, "_spawn", lambda camera_id: spawned.append(camera_id) or FakeProc())
    camera_id = stable_camera_id("mac", "aa:bb:cc:dd:ee:01")
    old = Camera(
        mac="aa:bb:cc:dd:ee:01", camera_id=camera_id,
        last_ip="10.0.0.5", stream_path="/onvif1",
    )
    moved = Camera(
        mac="aa:bb:cc:dd:ee:02", camera_id=camera_id,
        last_ip="10.0.0.5", stream_path="/onvif1",
    )
    try:
        rec.start([old])
        original = rec._procs[camera_id]
        rec.start([moved])

        assert original.terminated is False
        assert spawned == [camera_id]
        assert list(rec._procs) == [camera_id]
    finally:
        rec.stop()
