"""One failed camera must not monopolize the shared address-recovery worker."""

from unittest.mock import Mock

import pytest

from backend.app.db import registry, source_policy
from backend.app.services import address_recovery


def cameras():
    registry.init_db()
    result = []
    for index in (3, 4):
        cam = registry.upsert_camera(f'aa:bb:cc:dd:ee:0{index}',
            last_ip=f'192.0.2.{index}', stream_path='/live')
        pending = source_policy.begin_change(cam.camera_id, 'owned_rtsp', expected_revision=0)
        source_policy.activate(cam.camera_id, expected_revision=pending.revision)
        result.append(cam)
    return result


@pytest.mark.parametrize('trigger', ['pending', 'confirmed_dead'])
def test_failed_owned_repair_does_not_starve_another_cameras_dhcp_lookup(monkeypatch, trigger):
    failed, other = cameras()
    media = Mock(); media.stream_online.return_value = {}
    owned = Mock()
    owned.available.side_effect = lambda cid: not (trigger == 'confirmed_dead' and cid == failed.camera_id)
    def refresh(cid):
        if cid == failed.camera_id:
            raise OSError('synthetic repair failure')
    owned.refresh.side_effect = refresh
    lookup = Mock(return_value={other.mac:'192.0.2.44'})
    monkeypatch.setattr(address_recovery, 'find_addresses', lookup)
    monkeypatch.setattr(address_recovery, 'address_matches', lambda *args: True)
    recovery = address_recovery.AddressRecovery(media, Mock(), owned=owned)
    if trigger == 'pending':
        recovery.request_owned_apply((failed.camera_id,))
    recovery.tick(); recovery.tick()
    assert registry.get_camera_by_id(other.camera_id).last_ip == '192.0.2.44'
    assert registry.get_camera_by_id(failed.camera_id) == failed
    lookup.assert_called_once()
    calls = [call.args[0] for call in owned.refresh.call_args_list]
    assert calls.count(failed.camera_id) == (2 if trigger == 'pending' else 1)
    assert calls.count(other.camera_id) == 1
    assert recovery._owned_pending == {failed.camera_id}
    media.restart.assert_not_called()


def test_new_request_during_apply_is_preserved_without_second_attempt_in_same_tick():
    failed, _ = cameras()
    media = Mock(); media.stream_online.return_value = {}
    owned = Mock(); owned.available.return_value = True
    recovery = address_recovery.AddressRecovery(media, Mock(), owned=owned)
    owned.refresh.side_effect = lambda cid: recovery.request_owned_apply((cid,))
    recovery.request_owned_apply((failed.camera_id,))
    recovery.tick()
    owned.refresh.assert_called_once_with(failed.camera_id)
    assert recovery._owned_pending == {failed.camera_id}
    media.stream_online.assert_called_once()


def test_failed_owned_repair_does_not_starve_pending_registry_reconciliation():
    failed, _ = cameras()
    media = Mock()
    owned = Mock(); owned.refresh.side_effect = OSError('synthetic repair failure')
    recorder = Mock(paused=False)
    recovery = address_recovery.AddressRecovery(media, recorder, owned=owned)
    recovery.request_owned_apply((failed.camera_id,))
    recovery.request_apply()
    recovery.tick()
    media.restart.assert_called_once_with()
    recorder.start.assert_called_once_with()
    assert not recovery._pending_apply.is_set()
    assert recovery._owned_pending == {failed.camera_id}
