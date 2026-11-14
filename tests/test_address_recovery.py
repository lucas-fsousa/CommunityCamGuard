import threading
from unittest.mock import Mock

import pytest

from backend.app.db import registry, source_policy
from backend.app.discovery import address_refresh
from backend.app.services import address_recovery


@pytest.fixture(autouse=True)
def no_live_identity_audit(monkeypatch):
    monkeypatch.setattr(address_recovery, 'address_matches', lambda *args: True)


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


@pytest.mark.parametrize('mode',['native','owned_rtsp'])
def test_nonlegacy_source_is_not_treated_as_a_missing_hub_camera(monkeypatch,mode):
    cam=camera()
    pending=source_policy.begin_change(cam.camera_id,mode,expected_revision=0)
    source_policy.activate(cam.camera_id,expected_revision=pending.revision)
    lookup=Mock(side_effect=AssertionError('no legacy address scan for a native/owned source'))
    monkeypatch.setattr(address_recovery,'find_addresses',lookup)
    monkeypatch.setattr(address_recovery,'address_matches',lookup)
    media=Mock();media.stream_online.return_value={}
    recovery=address_recovery.AddressRecovery(media,Mock())
    recovery.tick();recovery.tick()
    lookup.assert_not_called()
    assert recovery._missing=={}


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


def test_healthy_wrong_identity_triggers_exact_mac_recovery_without_changing_other_fields(monkeypatch):
    cam = camera()
    lookup = Mock(return_value={cam.mac: '192.168.1.20'})
    monkeypatch.setattr(address_recovery, 'find_addresses', lookup)
    audit = Mock(return_value=False)
    monkeypatch.setattr(address_recovery, 'address_matches', audit, raising=False)
    media = Mock(); media.stream_online.return_value = {cam.camera_id: True}
    recorder = Mock(paused=False)
    recovery = address_recovery.AddressRecovery(media, recorder)
    recovery.tick()
    lookup.assert_not_called()
    recovery.tick()
    lookup.assert_called_once()
    updated = registry.get_camera_by_id(cam.camera_id)
    assert updated.last_ip == '192.168.1.20'
    assert (updated.camera_id, updated.name, updated.password, updated.capabilities) == (
        cam.camera_id, cam.name, cam.password, cam.capabilities)
    media.restart.assert_called_once()
    recorder.start.assert_called_once()


@pytest.mark.parametrize('matches', [True, None])
def test_healthy_matching_or_unknown_identity_does_not_scan_or_reload(monkeypatch, matches):
    cam = camera()
    audit = Mock(return_value=matches)
    lookup = Mock()
    monkeypatch.setattr(address_recovery, 'address_matches', audit)
    monkeypatch.setattr(address_recovery, 'find_addresses', lookup)
    media = Mock(); media.stream_online.return_value = {cam.camera_id: True}
    recovery = address_recovery.AddressRecovery(media, Mock())
    recovery.tick(); recovery.tick()
    audit.assert_called_once()
    lookup.assert_not_called()
    media.restart.assert_not_called()


def test_identity_audit_and_full_lookup_keep_their_cooldown(monkeypatch):
    cam = camera()
    now = [0.0]
    monkeypatch.setattr(address_recovery.time, 'monotonic', lambda: now[0])
    audit, lookup = Mock(return_value=False), Mock(return_value={})
    monkeypatch.setattr(address_recovery, 'address_matches', audit)
    monkeypatch.setattr(address_recovery, 'find_addresses', lookup)
    media = Mock(); media.stream_online.return_value = {cam.camera_id: True}
    recovery = address_recovery.AddressRecovery(media, Mock())
    recovery.tick(); recovery.tick()
    now[0] = 200
    recovery.tick()
    assert audit.call_count == lookup.call_count == 1
    now[0] = 301
    recovery.tick()
    assert audit.call_count == lookup.call_count == 2
    assert registry.get_camera_by_id(cam.camera_id).last_ip == cam.last_ip
    media.restart.assert_not_called()


def test_confirmed_existing_address_clears_identity_hint_without_reload(monkeypatch):
    cam = camera()
    monkeypatch.setattr(address_recovery, 'address_matches', lambda *args: False)
    lookup = Mock(return_value={cam.mac: cam.last_ip})
    monkeypatch.setattr(address_recovery, 'find_addresses', lookup)
    media = Mock(); media.stream_online.return_value = {cam.camera_id: True}
    recovery = address_recovery.AddressRecovery(media, Mock())
    recovery.tick(); recovery.tick(); recovery.tick()
    lookup.assert_called_once()
    assert not recovery._identity_mismatch and recovery._missing[cam.camera_id] == 0
    media.restart.assert_not_called()


@pytest.mark.parametrize('neighbor', [None, 'aa:bb:cc:dd:ee:01'])
def test_address_audit_unknown_or_matching_neighbor_needs_no_network(monkeypatch, neighbor):
    monkeypatch.setattr(address_refresh.active_scan, '_mac_for', lambda ip: neighbor)
    probe = Mock(side_effect=AssertionError('no network for absent/matching neighbor'))
    monkeypatch.setattr(address_refresh.active_scan, '_port_open', probe)
    result = address_refresh.address_matches('192.168.1.10', 'AA:BB:CC:DD:EE:01', threading.Event())
    assert result is (None if neighbor is None else True)


@pytest.mark.parametrize('actual,expected_match', [('aa:bb:cc:dd:ee:01', True), ('aa:bb:cc:dd:ee:02', False)])
def test_address_audit_allows_onvif_interface_match_before_reporting_wrong_neighbor(monkeypatch, actual, expected_match):
    monkeypatch.setattr(address_refresh.active_scan, '_mac_for', lambda ip: 'aa:bb:cc:dd:ee:02')
    monkeypatch.setattr(address_refresh.active_scan, '_port_open', lambda ip, port, timeout: port == 80)
    onvif = Mock(return_value=actual)
    monkeypatch.setattr(address_refresh.device, 'mac_address', onvif)
    assert address_refresh.address_matches('192.168.1.10', 'aa:bb:cc:dd:ee:01', threading.Event()) is expected_match
    onvif.assert_called_once_with('192.168.1.10', port=80, timeout=1.0)


def test_address_audit_cancellation_invalid_address_and_scan_lock_skip_probes(monkeypatch):
    from backend.app.discovery import scan_lock
    probe = Mock(side_effect=AssertionError('probe must not start'))
    monkeypatch.setattr(address_refresh.active_scan, '_mac_for', probe)
    stop = threading.Event(); stop.set()
    assert address_refresh.address_matches('192.168.1.10', 'mac', stop) is None
    stop.clear()
    for ip in ('bad', '::1'):
        assert address_refresh.address_matches(ip, 'mac', stop) is None
    with scan_lock:
        assert address_refresh.address_matches('192.168.1.10', 'mac', stop) is None
    probe.assert_not_called()


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
    assert not recovery._pending_apply.is_set()
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
    discovery.discovery_scan(request, discovery.ScanCredentials())
    resync.assert_called_once_with(request, address_changes=(cam.camera_id,))
    discovery.discovery_scan(request, discovery.ScanCredentials())
    resync.assert_called_once()


def test_scan_gate_prevents_overlap():
    from backend.app.discovery import scan_lock

    with scan_lock:
        assert address_refresh.find_addresses(
            {"aa:bb:cc:dd:ee:01"}, ["10.0.0.0/8"], threading.Event(),
        ) == {}


def test_manual_scan_ambiguous_identity_does_not_reload_media(monkeypatch):
    from types import SimpleNamespace

    from backend.app.api import discovery
    from backend.app.discovery.active_scan import ScannedHost

    cam = camera()
    monkeypatch.setattr(discovery.active_scan, "scan", lambda **kw: [
        ScannedHost(address="192.168.1.20", mac=cam.mac),
        ScannedHost(address="192.168.1.21", mac=cam.mac),
    ])
    resync = Mock()
    monkeypatch.setattr(discovery, "resync_services", resync)
    discovery.discovery_scan(SimpleNamespace(), discovery.ScanCredentials())
    resync.assert_not_called()
    assert registry.get_camera_by_id(cam.camera_id) == cam
