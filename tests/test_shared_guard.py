from dataclasses import replace

import pytest

from backend.app.drivers.contracts import ControlNotReady, ControlOperationError, WeeklySchedule
from backend.app.drivers.yoosee import controls, shared_control_reads, shared_control_write
from backend.app.drivers.yoosee.p2p.smart_protection_schedule import (
    native_smart_protection_schedule,
)
from tests.test_shared_volume import ENROLLMENT, VolumeSession
from tests.test_shared_volume import camera as camera

OLD = WeeklySchedule('08:00', '18:00', ('mon', 'wed'))
NEW = WeeklySchedule('22:30', '06:15', ('sun', 'fri'))


class GuardSession(VolumeSession):
    def __init__(self, fault=None, enabled=False, schedule=OLD):
        super().__init__(fault)
        self.result = replace(self.result, completion=replace(self.result.completion, value={
            'setVal': {'enable': int(enabled), 'plan': native_smart_protection_schedule(schedule),
                       'cryDetectEn': 2, 'unrelated': 'preserve'}}))

    def _change(self, key, value):
        if self.fault != 'readback':
            old = self.result.completion.value
            self.result = replace(self.result, completion=replace(self.result.completion,
                value={'setVal': {**old['setVal'], key: value}}))
        return object()

    def submit_guard_write(self, enabled):
        self.writes.append(('enabled', enabled))
        return self._change('enable', int(enabled))

    def submit_guard_schedule_write(self, schedule):
        self.writes.append(('plan', schedule))
        return self._change('plan', native_smart_protection_schedule(schedule))


@pytest.mark.parametrize('key,value', [('smart_protection', True), ('smart_protection_schedule', NEW)])
def test_guard_changes_use_current_account_and_confirm_state_without_touching_neighbors(camera, key, value):
    session = GuardSession()
    with shared_control_reads.publish(ENROLLMENT.camera_id, session):
        result = controls.write(camera, key, value)
    assert result.verified and result.application_acknowledged and result.changed
    assert result.value == value and result.previous_value == (False if key == 'smart_protection' else OLD)
    assert len(session.writes) == 1 and session.paths == ['ProWritable.guardParm'] * 2
    fields = session.result.completion.value['setVal']
    assert fields['cryDetectEn'] == 2 and fields['unrelated'] == 'preserve'
    if key == 'smart_protection':
        assert fields['plan'] == native_smart_protection_schedule(OLD)
    else:
        assert fields['enable'] == 0  # Editing a plan does not arm protection.


@pytest.mark.parametrize('key,value', [('smart_protection', False), ('smart_protection_schedule', OLD),
    ('smart_protection_schedule', WeeklySchedule('08:00', '18:00', ('wed', 'mon')))])
def test_unchanged_switch_or_reordered_weekdays_do_not_send_a_write(camera, key, value):
    session = GuardSession()
    with shared_control_reads.publish(ENROLLMENT.camera_id, session):
        result = controls.write(camera, key, value)
    assert not result.changed and result.verified and not session.writes
    assert session.paths == ['ProWritable.guardParm']


@pytest.mark.parametrize('key,value', [('smart_protection', True), ('smart_protection_schedule', NEW)])
@pytest.mark.parametrize('fault', ['rejected', 'readback', 'generation'])
def test_unconfirmed_guard_change_never_replays_or_falls_back(camera, monkeypatch, key, value, fault):
    monkeypatch.setattr(shared_control_write.time, 'sleep', lambda _: None)
    session = GuardSession(fault)
    with shared_control_reads.publish(ENROLLMENT.camera_id, session):
        with pytest.raises((ControlNotReady, ControlOperationError)):
            controls.write(camera, key, value)
    assert len(session.writes) == 1


@pytest.mark.parametrize('key,value', [('smart_protection', 1), ('smart_protection', 'true'),
    ('smart_protection_schedule', {}), ('smart_protection_schedule', '22:00')])
def test_invalid_guard_values_fail_before_preflight_with_public_driver_error(camera, key, value):
    session = GuardSession()
    with shared_control_reads.publish(ENROLLMENT.camera_id, session):
        with pytest.raises(ControlOperationError):
            controls.write(camera, key, value)
    assert not session.writes and not session.paths


@pytest.mark.parametrize('key,value', [('smart_protection', True), ('smart_protection_schedule', NEW)])
def test_missing_guard_preflight_never_sends_a_write(camera, key, value):
    session = GuardSession()
    session.result = replace(session.result, completion=replace(session.result.completion, value={}))
    with shared_control_reads.publish(ENROLLMENT.camera_id, session):
        with pytest.raises(ControlOperationError):
            controls.write(camera, key, value)
    assert not session.writes


def test_delayed_guard_readback_is_polled_without_repeating_write(camera, monkeypatch):
    session = GuardSession('readback')
    calls = []
    original = shared_control_write.read
    def read(*args):
        calls.append(True)
        if len(calls) == 3:
            session.fault = None
            session._change('enable', 1)
        return original(*args)
    monkeypatch.setattr(shared_control_write, 'read', read)
    monkeypatch.setattr(shared_control_write.time, 'sleep', lambda _: None)
    with shared_control_reads.publish(ENROLLMENT.camera_id, session):
        assert controls.write(camera, 'smart_protection', True).verified
    assert session.writes == [('enabled', True)] and len(calls) == 3


@pytest.mark.parametrize('key,value', [('smart_protection', True), ('smart_protection_schedule', NEW)])
def test_recovering_owner_cannot_open_standalone_guard_connection(camera, key, value):
    with shared_control_reads.reserve_native_owner(ENROLLMENT.camera_id):
        with pytest.raises(ControlNotReady):
            controls.write(camera, key, value)
