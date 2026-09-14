import threading
from unittest.mock import Mock

import pytest

from backend.app.db import registry
from backend.app.discovery import address_refresh
from backend.app.services import address_recovery


def camera():
    registry.init_db()
    return registry.upsert_camera(
        "aa:bb:cc:dd:ee:01", name="Test", last_ip="192.168.1.10",
        stream_path="/live", password="secret", capabilities={"driver": "generic"},
    )


def test_refresh_preserves_identity_and_credentials_and_rejects_races():
    cam = camera()
    assert registry.refresh_address(cam, "192.168.1.20")
    updated = registry.get_camera_by_id(cam.camera_id)
    assert (updated.camera_id, updated.name, updated.password, updated.capabilities) == (
        cam.camera_id, cam.name, cam.password, cam.capabilities,
    )
    assert not registry.refresh_address(cam, "192.168.1.30")
    registry.delete_camera_by_id(cam.camera_id)
    assert not registry.refresh_address(updated, "192.168.1.30")


def test_offline_debounce_cooldown_and_apply(monkeypatch):
    cam = camera()
    lookup = Mock(return_value={cam.mac: "192.168.1.20"})
    monkeypatch.setattr(address_recovery, "find_addresses", lookup)
    media = Mock()
    media.stream_online.return_value = {}
    rec = Mock(paused=False)
    recovery = address_recovery.AddressRecovery(media, rec)
    recovery.tick()
    lookup.assert_not_called()
    recovery.tick()
    assert registry.get_camera_by_id(cam.camera_id).last_ip == "192.168.1.20"
    media.restart.assert_called_once()
    rec.start.assert_called_once()
    recovery.tick()
    lookup.assert_called_once()


def test_healthy_or_unchanged_does_not_reload(monkeypatch):
    cam = camera()
    lookup = Mock(return_value={cam.mac: cam.last_ip})
    monkeypatch.setattr(address_recovery, "find_addresses", lookup)
    media = Mock()
    media.stream_online.return_value = {cam.camera_id: True}
    recovery = address_recovery.AddressRecovery(media, Mock())
    recovery.tick()
    recovery.tick()
    lookup.assert_not_called()
    media.stream_online.return_value = {}
    recovery.tick()
    recovery.tick()
    media.restart.assert_not_called()


def test_reload_failure_retried_without_scan_and_pause_preserved(monkeypatch):
    cam = camera()
    lookup = Mock(return_value={cam.mac: "192.168.1.20"})
    monkeypatch.setattr(address_recovery, "find_addresses", lookup)
    media = Mock()
    media.stream_online.return_value = {}
    media.restart.side_effect = [RuntimeError("offline"), None]
    rec = Mock(paused=True)
    recovery = address_recovery.AddressRecovery(media, rec)
    recovery.tick()
    with pytest.raises(RuntimeError):
        recovery.tick()
    recovery.tick()
    assert not recovery._pending_apply
    lookup.assert_called_once()
    rec.start.assert_not_called()


def test_scan_bounded_before_allocating_hosts():
    with pytest.raises(ValueError):
        address_refresh.targets(["10.0.0.0/8"])
    with pytest.raises(ValueError):
        address_refresh.targets(["::/0"])
    assert len(address_refresh.targets(["192.168.1.0/24"])) == 254


def test_duplicate_mac_rejected_and_no_credentials_sent(monkeypatch):
    monkeypatch.setattr(address_refresh.active_scan, "_port_open", lambda *args: True)
    monkeypatch.setattr(address_refresh.active_scan, "_mac_for", lambda ip: "aa:bb:cc:dd:ee:01")
    onvif = Mock()
    monkeypatch.setattr(address_refresh.device, "mac_address", onvif)
    found = address_refresh.find_addresses(
        {"aa:bb:cc:dd:ee:01"}, ["192.168.1.0/30"], threading.Event(),
    )
    assert found == {}
    onvif.assert_not_called()


def test_onvif_fallback_and_cancellation(monkeypatch):
    monkeypatch.setattr(address_refresh.active_scan, "_port_open", lambda *args: True)
    monkeypatch.setattr(address_refresh.active_scan, "_mac_for", lambda ip: None)
    onvif = Mock(return_value="aa:bb:cc:dd:ee:01")
    monkeypatch.setattr(address_refresh.device, "mac_address", onvif)
    stop = threading.Event()
    assert address_refresh.find_addresses(
        {"aa:bb:cc:dd:ee:01"}, ["192.168.1.20/32"], stop,
    ) == {"aa:bb:cc:dd:ee:01": "192.168.1.20"}
    stop.set()
    onvif.reset_mock()
    assert address_refresh.find_addresses(
        {"aa:bb:cc:dd:ee:01"}, ["192.168.1.20/32"], stop,
    ) == {}
    onvif.assert_not_called()


def test_manual_scan_reapplies_changed_address_only(monkeypatch):
    from types import SimpleNamespace

    from backend.app.api import discovery
    from backend.app.discovery.active_scan import ScannedHost

    cam = camera()
    monkeypatch.setattr(discovery.active_scan, "scan", lambda **kw: [
        ScannedHost(address="192.168.1.20", mac=cam.mac),
    ])
    resync = Mock()
    monkeypatch.setattr(discovery, "resync_services", resync)
    request = SimpleNamespace()
    discovery.discovery_scan(request)
    resync.assert_called_once_with(request)
    discovery.discovery_scan(request)
    resync.assert_called_once()


def test_scan_gate_prevents_overlap():
    from backend.app.discovery import scan_lock

    with scan_lock:
        assert address_refresh.find_addresses(
            {"aa:bb:cc:dd:ee:01"}, ["10.0.0.0/8"], threading.Event(),
        ) == {}
