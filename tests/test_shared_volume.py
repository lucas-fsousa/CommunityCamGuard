from dataclasses import replace
from types import SimpleNamespace

import pytest

from backend.app.drivers.contracts import ControlNotReady, ControlOperationError
from backend.app.drivers.yoosee import controls, shared_control_reads
from backend.app.drivers.yoosee.p2p.volume import VOLUME_RAW_BY_PERCENT
from backend.app.drivers.yoosee.p2p.volume_write_job import VolumeWriteResult
from tests.test_shared_control_reads import ENROLLMENT, Session


class VolumeSession(Session):
    def __init__(self, fault=None):
        super().__init__();self.fault=fault;self.writes=[]
    def submit_volume_write(self, percent):
        self.writes.append(percent)
        if self.fault != 'readback':
            self.result = replace(self.result, completion=replace(self.result.completion,
                value={'setVal':VOLUME_RAW_BY_PERCENT[percent]}))
        return object()
    def take_volume_write(self, ticket):
        if self.fault == 'generation':self.ready=False
        return VolumeWriteResult('response', 7 if self.fault == 'rejected' else 0, True)
    def cancel_volume_write(self, ticket):self.cancelled.append(ticket)


@pytest.fixture
def camera(monkeypatch):
    monkeypatch.setattr(controls, '_enrollment', lambda _: ENROLLMENT)
    monkeypatch.setattr(controls, 'run_with_fresh_access', lambda *a, **k: pytest.fail('parallel login'))
    return SimpleNamespace(camera_id=ENROLLMENT.camera_id)


def test_volume_write_reuses_owner_and_confirms_exact_readback(camera):
    session = VolumeSession()
    with shared_control_reads.publish(ENROLLMENT.camera_id, session):
        result = controls.write(camera, 'speaker_volume', 75)
    assert result.verified and result.changed and result.application_acknowledged
    assert result.previous_value == 50 and result.value == 75
    assert session.writes == [75] and session.paths == ['ProWritable.volume']*2


def test_same_raw_volume_does_not_send_a_write(camera):
    session = VolumeSession()
    with shared_control_reads.publish(ENROLLMENT.camera_id, session):
        result = controls.write(camera, 'speaker_volume', 50)
    assert result.verified and not result.changed and not session.writes


@pytest.mark.parametrize('fault', ['rejected', 'readback', 'generation'])
def test_failed_confirmation_never_repeats_or_falls_back(camera, fault):
    session = VolumeSession(fault)
    with shared_control_reads.publish(ENROLLMENT.camera_id, session):
        with pytest.raises((ControlOperationError, ControlNotReady)):
            controls.write(camera, 'speaker_volume', 75)
    assert session.writes == [75]
