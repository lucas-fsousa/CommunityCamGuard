from dataclasses import replace

import pytest

from backend.app.drivers.contracts import ControlNotReady, ControlOperationError
from backend.app.drivers.yoosee import controls, shared_control_reads
from backend.app.drivers.yoosee.p2p.orientation import ORIENTATION_VALUES
from tests.test_shared_volume import ENROLLMENT, VolumeSession
from tests.test_shared_volume import camera as camera


class OrientationSession(VolumeSession):
    def __init__(self, fault=None, initial='normal'):
        super().__init__(fault)
        self.result = replace(self.result, completion=replace(self.result.completion,
            value={'setVal':{'multiFlip':ORIENTATION_VALUES[initial]}}))

    def submit_orientation_write(self, value):
        self.writes.append(value)
        if self.fault != 'readback':
            self.result = replace(self.result, completion=replace(self.result.completion,
                value={'setVal':{'multiFlip':ORIENTATION_VALUES[value]}}))
        return object()


def test_orientation_change_uses_current_owner_and_confirms_exact_state(camera):
    session = OrientationSession()
    with shared_control_reads.publish(ENROLLMENT.camera_id, session):
        result = controls.write(camera, 'orientation', 'inverted')
    assert result.verified and result.changed and result.application_acknowledged
    assert result.previous_value == 'normal' and result.value == 'inverted'
    assert result.native_previous_value == 1 and result.native_requested_value == 3
    assert session.writes == ['inverted'] and session.paths == ['ProWritable.videoParm']*2


def test_same_orientation_only_reads_state(camera):
    session = OrientationSession(initial='inverted')
    with shared_control_reads.publish(ENROLLMENT.camera_id, session):
        result = controls.write(camera, 'orientation', 'inverted')
    assert result.verified and not result.changed and not session.writes


@pytest.mark.parametrize('fault', ['rejected', 'readback', 'generation'])
def test_failed_orientation_confirmation_does_not_repeat_or_fall_back(camera, fault):
    session = OrientationSession(fault)
    with shared_control_reads.publish(ENROLLMENT.camera_id, session):
        with pytest.raises((ControlOperationError, ControlNotReady)):
            controls.write(camera, 'orientation', 'inverted')
    assert session.writes == ['inverted']


def test_missing_orientation_preflight_cannot_send_anything(camera):
    session = OrientationSession()
    session.result = replace(session.result, completion=replace(session.result.completion, value={'setVal':{}}))
    with shared_control_reads.publish(ENROLLMENT.camera_id, session):
        with pytest.raises(ControlOperationError):controls.write(camera, 'orientation', 'inverted')
    assert not session.writes
