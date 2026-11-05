from dataclasses import replace

import pytest

from backend.app.drivers.contracts import ControlNotReady, ControlOperationError
from backend.app.drivers.yoosee import controls, shared_control_reads
from backend.app.drivers.yoosee.p2p.siren_pulse_inbox import SirenPulseInbox
from backend.app.drivers.yoosee.p2p.siren_pulse_job import SirenPulseResult, SirenPulseSpec
from tests.test_shared_volume import ENROLLMENT, VolumeSession
from tests.test_shared_volume import camera as camera


class Session(VolumeSession):
    def __init__(self, fault=None, state=1):
        super().__init__(fault)
        self.result = replace(self.result, completion=replace(self.result.completion,
            value={'t':1, 'stVal':state}))

    def submit_siren_pulse(self, seconds):
        self.writes.append(seconds)
        return object()

    def take_siren_pulse(self, ticket):
        if self.fault == 'generation': self.ready = False
        if self.fault == 'readback':
            self.result = replace(self.result, completion=replace(self.result.completion,
                value={'t':1, 'stVal':2}))
        return SirenPulseResult('completed' if self.fault != 'rejected' else 'cancelled', True, 1,
            self.fault != 'unknown_off', 0, 0, True, False)

    def cancel_siren_pulse(self, ticket):
        self.cancelled.append(ticket)


@pytest.mark.parametrize('seconds', [2,5,10])
def test_driver_pulse_uses_same_owner_and_confirms_final_off(camera, seconds):
    session = Session()
    with shared_control_reads.publish(ENROLLMENT.camera_id, session):
        result = controls.write(camera, 'siren_pulse', seconds)
    assert result.verified and result.value == seconds and result.application_acknowledged
    assert session.writes == [seconds] and session.paths == ['Action.expelCtrl']*2


@pytest.mark.parametrize('fault', ['rejected','unknown_off','generation','readback'])
def test_unconfirmed_pulse_never_repeats_on_or_falls_back(camera, fault):
    session = Session(fault)
    with shared_control_reads.publish(ENROLLMENT.camera_id, session):
        with pytest.raises((ControlNotReady, ControlOperationError)):
            controls.write(camera, 'siren_pulse', 2)
    assert session.writes == [2]


def test_existing_active_siren_is_not_taken_over(camera):
    session = Session(state=2)
    with shared_control_reads.publish(ENROLLMENT.camera_id, session):
        with pytest.raises(ControlNotReady): controls.write(camera, 'siren_pulse', 2)
    assert not session.writes


@pytest.mark.parametrize('value', [True,'2',1,11,None])
def test_invalid_pulse_is_rejected_before_state_read(camera, value):
    session = Session()
    with shared_control_reads.publish(ENROLLMENT.camera_id, session):
        with pytest.raises(ControlOperationError): controls.write(camera, 'siren_pulse', value)
    assert not session.writes and not session.paths


def test_recovering_owner_does_not_open_an_independent_siren_session(camera):
    with shared_control_reads.reserve_native_owner(ENROLLMENT.camera_id):
        with pytest.raises(ControlNotReady): controls.write(camera, 'siren_pulse', 2)


def test_claimed_cancellation_holds_slot_until_off_is_known():
    inbox = SirenPulseInbox(1)
    spec = SirenPulseSpec(123, 456, 2)
    ticket = inbox.submit(spec)
    assert inbox.claim() is ticket
    inbox.cancel(ticket)
    with pytest.raises(ValueError): inbox.submit(spec)
    inbox.finish(ticket, SirenPulseResult('cancelled',True,1,True,None,0,False,False))
    next_ticket = inbox.submit(spec)
    inbox.claim()
    inbox.finish(next_ticket, SirenPulseResult('off_unconfirmed',True,2,False,0,None,False,False))
    assert not inbox.take(next_ticket).off_confirmed
    with pytest.raises(ValueError): inbox.submit(spec)
