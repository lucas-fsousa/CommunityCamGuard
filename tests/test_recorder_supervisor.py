"""Supervision guards that keep a runaway recorder ffmpeg from taking down the host.

A muxing-queue balloon in one ffmpeg once grew to ~2 GB RSS and, with no cgroup cap, made the
kernel OOM-kill *globally* — killing host processes outside the container. These cover the
in-process half of the fix: kill a process that grows past the ceiling, and back off instead of
tight-looping a respawn.
"""
import subprocess

import pytest

from backend.app.camera_identity import stable_camera_id
from backend.app.db.registry import Camera
from backend.app.recording import recorder as rec_mod
from backend.app.recording.recorder import Recorder


class FakeProc:
    """Stands in for a Popen: `alive` drives poll(), `pid` is looked up by the RSS probe."""

    _next_pid = 1000

    def __init__(self, alive=True):
        FakeProc._next_pid += 1
        self.pid = FakeProc._next_pid
        self.alive = alive
        self.killed = False
        self.terminated = False

    def poll(self):
        return None if self.alive else 1

    def kill(self):
        self.killed = True
        self.alive = False

    def terminate(self):
        self.terminated = True
        self.alive = False

    def wait(self, timeout=None):
        return 0


@pytest.fixture
def recorder(tmp_path, monkeypatch):
    monkeypatch.setenv("RECORDINGS_DIR", str(tmp_path / "recordings"))
    monkeypatch.setenv("DB_PATH", str(tmp_path / "ccg.db"))
    rec = Recorder()
    rec.root = tmp_path / "recordings"
    rec._cameras = {CAMERA_ID: Camera(mac=MAC, camera_id=CAMERA_ID)}
    rec.spawned = []

    def fake_spawn(mac):
        p = FakeProc()
        rec.spawned.append(mac)
        return p

    monkeypatch.setattr(rec, "_spawn", fake_spawn)
    return rec


MAC = "aa:bb:cc:dd:ee:ff"
CAMERA_ID = stable_camera_id("mac", MAC)


def _set_rss(monkeypatch, mb):
    monkeypatch.setattr(rec_mod, "_rss_bytes", lambda pid: mb * 1024 * 1024)


def test_watchdog_leaves_a_lean_ffmpeg_alone(recorder, monkeypatch):
    recorder._spawn_locked(CAMERA_ID)
    proc = recorder._procs[CAMERA_ID]
    _set_rss(monkeypatch, 40)  # a healthy remux
    recorder._watchdog_locked()
    assert not proc.killed and recorder._procs[CAMERA_ID] is proc


def test_watchdog_kills_a_ballooning_ffmpeg(recorder, monkeypatch):
    recorder._spawn_locked(CAMERA_ID)
    proc = recorder._procs[CAMERA_ID]
    _set_rss(monkeypatch, 512)  # past _RSS_LIMIT_BYTES (256 MB)
    recorder._watchdog_locked()
    assert proc.killed
    assert CAMERA_ID not in recorder._procs  # bookkeeping done by the watchdog itself
    assert recorder._fails[CAMERA_ID] == 1   # counted once, so repeats back off


def test_watchdog_kill_is_not_double_counted(recorder, monkeypatch):
    """The watchdog books the failure; _spawn_locked must not book it a second time."""
    recorder._spawn_locked(CAMERA_ID)
    _set_rss(monkeypatch, 512)
    recorder._watchdog_locked()
    recorder._spawn_locked(CAMERA_ID)
    assert recorder._fails[CAMERA_ID] == 1


def test_backoff_grows_then_resets_after_a_healthy_run(recorder, monkeypatch):
    clock = {"t": 1000.0}
    monkeypatch.setattr(rec_mod.time, "monotonic", lambda: clock["t"])

    delays = []
    for _ in range(4):
        recorder._spawn_locked(CAMERA_ID)               # spawn
        assert CAMERA_ID in recorder._procs
        recorder._procs[CAMERA_ID].alive = False        # dies immediately
        recorder._spawn_locked(CAMERA_ID)               # notices the death, books the failure
        assert CAMERA_ID not in recorder._procs, "must not respawn in the pass that saw the death"
        delays.append(recorder._retry_at[CAMERA_ID] - clock["t"])
        clock["t"] += delays[-1]                  # wait out the backoff

    assert delays == [5.0, 10.0, 20.0, 40.0]

    # A run that lasts past the healthy threshold clears the penalty.
    recorder._spawn_locked(CAMERA_ID)
    clock["t"] += rec_mod._HEALTHY_AFTER + 1
    recorder._procs[CAMERA_ID].alive = False
    recorder._spawn_locked(CAMERA_ID)
    assert recorder._fails[CAMERA_ID] == 0
    assert recorder._retry_at[CAMERA_ID] == clock["t"]  # eligible immediately


def test_backoff_is_honoured_before_it_expires(recorder, monkeypatch):
    clock = {"t": 1000.0}
    monkeypatch.setattr(rec_mod.time, "monotonic", lambda: clock["t"])
    recorder._spawn_locked(CAMERA_ID)
    recorder._procs[CAMERA_ID].alive = False
    recorder._spawn_locked(CAMERA_ID)             # books failure, retry at +5s
    spawned = len(recorder.spawned)

    clock["t"] += 4.0
    recorder._spawn_locked(CAMERA_ID)
    assert len(recorder.spawned) == spawned, "still inside the backoff window"

    clock["t"] += 2.0
    recorder._spawn_locked(CAMERA_ID)
    assert len(recorder.spawned) == spawned + 1


def test_backoff_is_capped(recorder):
    recorder._fails[CAMERA_ID] = 50
    assert recorder._retry_delay(CAMERA_ID) == rec_mod._BACKOFF_MAX


def test_pause_clears_the_backoff(recorder, monkeypatch):
    clock = {"t": 1000.0}
    monkeypatch.setattr(rec_mod.time, "monotonic", lambda: clock["t"])
    recorder._spawn_locked(CAMERA_ID)
    recorder._procs[CAMERA_ID].alive = False
    recorder._spawn_locked(CAMERA_ID)
    assert recorder._fails[CAMERA_ID] == 1

    recorder.pause()                              # a deliberate stop is not a failure
    assert recorder._fails == {} and recorder._retry_at == {}


def test_oversized_log_is_truncated(recorder):
    path = recorder._log_path(CAMERA_ID)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x" * (rec_mod._MAX_LOG_BYTES + 1))
    recorder._trim_log(CAMERA_ID)
    assert path.stat().st_size == 0


def test_small_log_is_kept(recorder):
    path = recorder._log_path(CAMERA_ID)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x" * 100)
    recorder._trim_log(CAMERA_ID)
    assert path.stat().st_size == 100


def test_rss_probe_returns_none_for_a_dead_pid():
    assert rec_mod._rss_bytes(2 ** 30) is None


def test_rss_probe_reads_a_real_process():
    proc = subprocess.Popen(["sleep", "5"])
    try:
        rss = rec_mod._rss_bytes(proc.pid)
        assert rss is not None and 0 < rss < rec_mod._RSS_LIMIT_BYTES
    finally:
        proc.kill()
        proc.wait()
