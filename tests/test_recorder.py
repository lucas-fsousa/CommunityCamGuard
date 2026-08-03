"""Tests for the recorder (backend/app/recording/recorder.py) beyond the supervisor: the ffmpeg
command build, segment indexing, and the start/pause/resume/stop lifecycle. subprocess is faked,
so no real ffmpeg runs.
"""
from datetime import datetime

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
        return FakeProc()

    monkeypatch.setattr(recorder.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(recorder.go2rtc, "restream_rtsp_url", lambda mac: "rtsp://127.0.0.1:3203/cam_x")
    rec = Recorder(segment_seconds=300)
    rec._spawn("aa:bb:cc:dd:ee:01")
    cmd = captured["cmd"]
    assert cmd[0] == "ffmpeg"
    assert "-c:v" in cmd and cmd[cmd.index("-c:v") + 1] == "copy"          # remux, no re-encode
    assert cmd[cmd.index("-i") + 1] == "rtsp://127.0.0.1:3203/cam_x"       # the go2rtc restream
    assert "1" == cmd[cmd.index("-use_wallclock_as_timestamps") + 1]      # timestamp repair
    assert cmd[cmd.index("-segment_time") + 1] == "300"                    # from segment_seconds
    # crash-safe fragmented MP4 (ADR 0004)
    assert "movflags=+frag_keyframe+empty_moov+default_base_moof" in cmd
    assert cmd[-1].endswith("%Y%m%d_%H%M%S.mp4")                           # strftime output template


# --- _index: pick up finalized segments ---------------------------------------------

def _make_segment(rec, mac, when="20260801_120000"):
    started = datetime.strptime(when, "%Y%m%d_%H%M%S")
    d = rec.root / recorder._safe_mac(mac) / started.strftime("%Y-%m-%d") / started.strftime("%H")
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
    monkeypatch.setattr(recorder.go2rtc, "restream_rtsp_url", lambda mac: "rtsp://127.0.0.1:3203/cam_x")
    rec = Recorder(segment_seconds=300, maint_interval=30)   # slow loop: won't interfere
    cam = Camera(mac="aa:bb:cc:dd:ee:01", last_ip="10.0.0.5", stream_path="/onvif1")
    try:
        macs = rec.start(cameras=[cam])
        assert macs == ["aa:bb:cc:dd:ee:01"]
        assert rec.is_recording("aa:bb:cc:dd:ee:01") is True
        assert rec.paused is False

        rec.pause()
        assert rec.paused is True
        assert rec.is_recording("aa:bb:cc:dd:ee:01") is False   # process terminated
        rec.resume()
        assert rec.paused is False
    finally:
        rec.stop()   # joins the maintenance thread, terminates procs, final index
