from dataclasses import replace

import pytest

from backend.app.drivers.contracts import ControlNotReady, ControlOperationError
from backend.app.drivers.yoosee import controls, shared_control_reads, shared_floodlight
from backend.app.drivers.yoosee.p2p.lamp_read_job import LampReadResult
from tests.test_shared_volume import ENROLLMENT, VolumeSession
from tests.test_shared_volume import camera as camera


class LightSession(VolumeSession):
    def __init__(self, fault=None, enabled=False):
        super().__init__(fault)
        self.enabled = enabled
        self.reply = LampReadResult('response', None, True, 0, True)

    def submit_lamp_write(self, enabled):
        self.writes.append(enabled)
        if self.fault != 'readback': self.enabled = enabled
        if self.fault == 'reject': self.reply = replace(self.reply, status='rejected', error_code=7)
        return object()

    def take_lamp_write(self, ticket):
        if self.fault == 'generation': self.ready = False
        return self.reply

    def cancel_lamp_write(self, ticket): self.cancelled.append(ticket)

    def take_lamp_read(self, ticket):
        return LampReadResult('response', self.enabled, True)


@pytest.mark.parametrize('enabled', [False, True])
def test_boolean_change_reuses_account_with_type12_preflight_and_verified_readback(camera, enabled):
    session = LightSession(enabled=not enabled)
    with shared_control_reads.publish(ENROLLMENT.camera_id, session):
        result = controls.write(camera, 'white_light', enabled)
    assert result.verified and result.changed and result.application_acknowledged
    assert result.value is enabled and result.previous_value is not enabled
    assert session.writes == [enabled] and session.paths == ['lamp:type12']*2


def test_same_lamp_state_only_reads(camera):
    session = LightSession()
    with shared_control_reads.publish(ENROLLMENT.camera_id, session):
        result = controls.write(camera, 'white_light', False)
    assert not result.changed and result.verified and not session.writes


@pytest.mark.parametrize('fault', ['reject', 'readback', 'generation'])
def test_lamp_failure_never_repeats_a_write_or_opens_another_login(camera, monkeypatch, fault):
    monkeypatch.setattr(shared_floodlight.time, 'sleep', lambda _: None)
    session = LightSession(fault)
    with shared_control_reads.publish(ENROLLMENT.camera_id, session):
        with pytest.raises((ControlNotReady,ControlOperationError)):
            controls.write(camera, 'white_light', True)
    assert session.writes == [True]


def test_missing_write_reply_requires_actual_send_and_exact_readback_without_claiming_application_ack(camera):
    session = LightSession()
    session.reply = LampReadResult('timeout', sent=True)
    with shared_control_reads.publish(ENROLLMENT.camera_id, session):
        result = controls.write(camera, 'white_light', True)
    assert result.verified and result.changed and not result.application_acknowledged
    assert result.error_code is None and session.writes == [True]


@pytest.mark.parametrize('status,sent', [('timeout',False),('cancelled',True),('invalid_response',True),('unavailable',False)])
def test_no_send_cancel_or_invalid_reply_cannot_be_resolved_by_unrelated_state(camera,status,sent):
    session = LightSession()
    session.reply = LampReadResult(status, sent=sent)
    with shared_control_reads.publish(ENROLLMENT.camera_id, session):
        with pytest.raises(ControlOperationError): controls.write(camera, 'white_light', True)
    assert session.paths == ['lamp:type12']  # No readback to waive rejection/cancellation.


@pytest.mark.parametrize('value', [1, 0, 'true', None])
def test_invalid_lamp_input_preserves_public_driver_error_before_any_io(camera,value):
    session = LightSession()
    with shared_control_reads.publish(ENROLLMENT.camera_id, session):
        with pytest.raises(ControlOperationError): controls.write(camera,'white_light',value)
    assert not session.paths and not session.writes


def test_recovering_native_owner_never_falls_back_to_standalone(camera):
    with shared_control_reads.reserve_native_owner(ENROLLMENT.camera_id):
        with pytest.raises(ControlNotReady): controls.write(camera,'white_light',True)
