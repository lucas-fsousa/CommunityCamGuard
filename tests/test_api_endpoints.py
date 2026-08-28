"""Endpoint tests for the routes not covered elsewhere: delete/probe/ptz/reboot, discovery,
media/storage, and recordings/file. Route functions are called directly (FastAPI allows it) with
a stub Request; drivers and the network are monkeypatched, so these are fast and offline.
"""
from types import SimpleNamespace

import pytest

from backend.app.api import routes
from backend.app.db import registry

MAC = "aa:bb:cc:dd:ee:ff"


def _req(**state):
    """A stub Request whose app.state carries whatever services a handler reads."""
    return SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(**state)))


def _seed_camera(**kw):
    registry.init_db()
    return registry.upsert_camera(MAC, last_ip=kw.pop("last_ip", "192.168.1.50"), **kw)


class FakeDriver:
    def __init__(self, *, ptz_result=True, reboot_result=True, raises=None):
        self._ptz, self._reboot, self._raises = ptz_result, reboot_result, raises

    def ptz(self, cam, direction, action="step"):
        if self._raises:
            raise self._raises
        return self._ptz

    def reboot(self, cam):
        if self._raises:
            raise self._raises
        return self._reboot


# --- delete -------------------------------------------------------------------------

def test_delete_camera_removes_it():
    camera = _seed_camera()
    out = routes.delete_camera(camera.camera_id, _req(media=None, rec=None))
    assert out == {"ok": True}
    assert registry.get_camera(MAC) is None


# --- probe --------------------------------------------------------------------------

def test_probe_unknown_camera_is_404():
    registry.init_db()
    with pytest.raises(routes.HTTPException) as ei:
        routes.probe_camera("00:00:00:00:00:00")
    assert ei.value.status_code == 404


def test_probe_camera_without_ip_is_409():
    camera = _seed_camera(last_ip="")
    with pytest.raises(routes.HTTPException) as ei:
        routes.probe_camera(camera.camera_id)
    assert ei.value.status_code == 409


def test_probe_camera_success(monkeypatch):
    camera = _seed_camera()
    monkeypatch.setattr(routes, "_probe_and_store", lambda cam: registry.get_camera(cam.mac))
    out = routes.probe_camera(camera.camera_id)
    assert out["mac"] == MAC
    assert out["id"] == camera.camera_id


# --- ptz ----------------------------------------------------------------------------

def test_ptz_unknown_camera_is_404():
    registry.init_db()
    with pytest.raises(routes.HTTPException) as ei:
        routes.ptz_move("00:00:00:00:00:00", routes.PtzIn(direction="left", action="start"))
    assert ei.value.status_code == 404


def test_ptz_success(monkeypatch):
    camera = _seed_camera()
    monkeypatch.setattr(routes.drivers, "for_camera", lambda cam: FakeDriver(ptz_result=True))
    out = routes.ptz_move(camera.camera_id, routes.PtzIn(direction="Left", action="Start"))
    assert out == {"ok": True, "action": "start", "direction": "left"}


def test_ptz_unsupported_is_501(monkeypatch):
    camera = _seed_camera()
    monkeypatch.setattr(routes.drivers, "for_camera",
                        lambda cam: FakeDriver(raises=routes.drivers.Unsupported("ptz")))
    with pytest.raises(routes.HTTPException) as ei:
        routes.ptz_move(camera.camera_id, routes.PtzIn(direction="left", action="start"))
    assert ei.value.status_code == 501


def test_ptz_bad_direction_is_400(monkeypatch):
    camera = _seed_camera()
    monkeypatch.setattr(routes.drivers, "for_camera",
                        lambda cam: FakeDriver(raises=ValueError("unknown direction")))
    with pytest.raises(routes.HTTPException) as ei:
        routes.ptz_move(camera.camera_id, routes.PtzIn(direction="sideways", action="step"))
    assert ei.value.status_code == 400


def test_ptz_rejected_is_502(monkeypatch):
    camera = _seed_camera()
    monkeypatch.setattr(routes.drivers, "for_camera", lambda cam: FakeDriver(ptz_result=False))
    with pytest.raises(routes.HTTPException) as ei:
        routes.ptz_move(camera.camera_id, routes.PtzIn(direction="left", action="start"))
    assert ei.value.status_code == 502


# --- reboot -------------------------------------------------------------------------

def test_reboot_success(monkeypatch):
    camera = _seed_camera()
    monkeypatch.setattr(routes.drivers, "for_camera", lambda cam: FakeDriver(reboot_result=True))
    assert routes.reboot_camera(camera.camera_id) == {"ok": True, "rebooting": True}


def test_reboot_unsupported_is_501(monkeypatch):
    camera = _seed_camera()
    monkeypatch.setattr(routes.drivers, "for_camera",
                        lambda cam: FakeDriver(raises=routes.drivers.Unsupported("reboot")))
    with pytest.raises(routes.HTTPException) as ei:
        routes.reboot_camera(camera.camera_id)
    assert ei.value.status_code == 501


def test_reboot_unknown_camera_is_404():
    registry.init_db()
    with pytest.raises(routes.HTTPException) as ei:
        routes.reboot_camera("00:00:00:00:00:00")
    assert ei.value.status_code == 404


def test_legacy_mac_reference_remains_temporarily_accepted(monkeypatch):
    """Pre-camera-id API clients retain a bounded exact-MAC compatibility path."""
    _seed_camera()
    monkeypatch.setattr(routes.drivers, "for_camera", lambda cam: FakeDriver(reboot_result=True))
    assert routes.reboot_camera(MAC) == {"ok": True, "rebooting": True}


# --- discovery ----------------------------------------------------------------------

def test_discovery_scan_returns_configured_and_candidates(monkeypatch):
    registry.init_db()
    monkeypatch.setattr(routes.active_scan, "scan", lambda **k: [])
    monkeypatch.setattr(routes.registry, "reconcile", lambda hosts, on_rekey=None: ([], []))
    out = routes.discovery_scan(_req(media=None, rec=None))
    assert out == {"configured": [], "candidates": []}


# --- media / storage ----------------------------------------------------------------

def test_media_restart_ok():
    out = routes.media_restart(_req(media=None, rec=None))
    assert out == {"ok": True}


def test_storage_status_503_without_monitor():
    with pytest.raises(routes.HTTPException) as ei:
        routes.storage_status(_req())
    assert ei.value.status_code == 503


def test_storage_status_returns_monitor_state():
    # the handler returns state().__dict__, so use an object whose real __dict__ is the payload
    state = SimpleNamespace(percent=42, status="ok", paused=False)
    monitor = SimpleNamespace(state=lambda: state)
    out = routes.storage_status(_req(storage=monitor))
    assert out == {"percent": 42, "status": "ok", "paused": False}


# --- recordings/file ----------------------------------------------------------------

def test_recording_file_rejects_path_outside_root():
    with pytest.raises(routes.HTTPException) as ei:
        routes.recording_file(path="/etc/passwd")
    assert ei.value.status_code == 404
