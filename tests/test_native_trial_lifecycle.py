"""Mocked operator lifecycle: no Docker call, camera or production database."""

import sqlite3
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from backend.app import drivers
from backend.app.db import p2p, registry
from scripts import native_dashboard_trial as module
from tests.test_read_preparation import ENROLLMENT


@pytest.mark.parametrize("clean", [True, False])
def test_original_owner_is_acquired_after_stop_and_released_before_safe_restore(tmp_path, monkeypatch, clean):
    database = tmp_path / "original.db"
    with sqlite3.connect(database) as conn:
        conn.execute("CREATE TABLE fixture (id INTEGER)")
    config = tmp_path / "go2rtc.yaml"
    config.write_text("streams: {}")
    trial = tmp_path / "trial"
    monkeypatch.setattr(module.os, "environ", dict(module.os.environ))
    events = []
    class Containers:
        def __init__(self):
            self.app = {"Id":"app", "Config":{"Env":[]}}
            self.hub = {"Id":"hub", "Image":"fixture"}
        def stop_all(self): events.append("stop")
    class Owner:
        def __init__(self, path): assert path == database
        def acquire(self):
            assert events == ["stop"]
            events.append("acquire")
        def close(self): events.append("release")
    def run(*args, **kwargs):
        assert events == ["stop", "acquire"]
        assert kwargs["ptz_directions"] == frozenset({"right"})
        if not clean:
            raise RuntimeError("uncertain native shutdown")
        module.write_private_config(trial / "server-stopped.json", '{"stopped":true}')
    def restore(path):
        assert events[-1] == "release"
        assert path == trial
        events.append("restore")
    monkeypatch.setattr(module, "get_settings", Mock(return_value=SimpleNamespace(
        db_path=database, go2rtc_config=config)))
    monkeypatch.setattr(module, "TrialContainers", Containers)
    monkeypatch.setattr(module, "ServerOwner", Owner)
    monkeypatch.setattr(module, "run_server", run)
    monkeypatch.setattr(module, "restore_trial", restore)
    monkeypatch.setattr(registry, "get_camera_by_id", lambda _: SimpleNamespace(
        camera_id=ENROLLMENT.camera_id, capabilities={"ptz":True}))
    monkeypatch.setattr(registry, "list_cameras", lambda: [])
    monkeypatch.setattr(p2p, "read_registered_enrollment", lambda _: ENROLLMENT)
    monkeypatch.setattr(drivers, "for_camera", lambda _: SimpleNamespace(key="yoosee"))
    monkeypatch.setattr("sys.argv", ["trial", "--camera-id", ENROLLMENT.camera_id,
        "--directory", str(trial), "--seconds", "30", "--native-av",
        "--ptz-direction", "right", "--confirm-interruption"])
    if clean:
        module.main()
        assert events == ["stop", "acquire", "release", "restore"]
    else:
        with pytest.raises(RuntimeError, match="uncertain"):
            module.main()
        assert events == ["stop", "acquire", "release"]  # ExecStopPost must reap first


def test_motion_requires_native_av_before_any_container_operation(monkeypatch):
    monkeypatch.setattr(module, "TrialContainers", lambda: pytest.fail("must reject before Docker"))
    monkeypatch.setattr("sys.argv", ["trial", "--camera-id", ENROLLMENT.camera_id,
        "--directory", "unused", "--ptz-direction", "right", "--confirm-interruption"])
    with pytest.raises(SystemExit):
        module.main()


@pytest.mark.parametrize("args", [
    ["--recover-at", "20"],
    ["--recover-at", "1", "--native-av"],
    ["--recover-at", "890", "--native-av"],
])
def test_recovery_arguments_are_checked_before_container_operations(monkeypatch, args):
    monkeypatch.setattr(module, "TrialContainers", lambda: pytest.fail("must reject before Docker"))
    monkeypatch.setattr("sys.argv", ["trial", "--camera-id", ENROLLMENT.camera_id,
        "--directory", "unused", "--confirm-interruption", *args])
    with pytest.raises(SystemExit):
        module.main()
