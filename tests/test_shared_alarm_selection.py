from dataclasses import replace

import pytest

from backend.app.drivers.contracts import ControlNotReady, ControlOperationError
from backend.app.drivers.yoosee import (
    alarm_resource_controls,
    controls,
    shared_alarm_catalog,
    shared_alarm_selection,
    shared_control_reads,
)
from backend.app.drivers.yoosee.p2p.alarm_voice_catalog import P2PAlarmVoiceCatalog
from backend.app.drivers.yoosee.p2p.model_control_specs import AlarmVoiceWriteSpec
from tests.test_shared_volume import ENROLLMENT, VolumeSession
from tests.test_shared_volume import camera as camera
from tests.test_volume_write_job import fixture, response
from tests.test_yoosee_alarm_resource_controls import resource

ITEM = resource(7)
OLD = resource(8)


class SelectionSession(VolumeSession):
    def __init__(self, fault=None, initial=OLD):
        super().__init__(fault)
        self.result = replace(self.result, completion=replace(self.result.completion,
            value={'t':12, 'setVal':{'supportFunc':1, 'resId':initial.resource_id}}))

    def submit_alarm_voice_write(self, value):
        self.writes.append(value)
        if self.fault != 'readback':
            actual = resource(7, opaque=999) if self.fault == 'wrong_identity' else value
            self.result = replace(self.result, completion=replace(self.result.completion,
                value={'t':12, 'setVal':{'supportFunc':1, 'resId':actual.resource_id}}))
        return object()


@pytest.fixture
def catalogue(monkeypatch):
    calls = []
    monkeypatch.setattr(alarm_resource_controls, 'managed', lambda _: False)
    def read(entry):
        calls.append(entry)
        return P2PAlarmVoiceCatalog(entry.device_id, 1, 0, True, (ITEM,))
    monkeypatch.setattr(shared_alarm_selection, 'read_catalog', read)
    return calls


def test_native_selection_resolves_fresh_key_and_confirms_exact_resource(camera, catalogue):
    session = SelectionSession()
    with shared_control_reads.publish(ENROLLMENT.camera_id, session):
        result = controls.write(camera, 'alarm_voice', ITEM.key)
    assert result.verified and result.changed and result.application_acknowledged
    assert result.native_previous_value == 8 and result.native_requested_value == 7
    assert session.writes == [ITEM] and session.paths == ['ProWritable.resFile']*2
    assert catalogue == [ENROLLMENT]


def test_same_resource_is_read_only(camera, catalogue):
    session = SelectionSession(initial=ITEM)
    with shared_control_reads.publish(ENROLLMENT.camera_id, session):
        result = controls.write(camera, 'alarm_voice', ITEM.key)
    assert result.verified and not result.changed and not session.writes


@pytest.mark.parametrize('fault', ['rejected', 'readback', 'generation', 'wrong_identity'])
def test_unknown_or_wrong_identity_never_replays_or_falls_back(camera, catalogue, monkeypatch, fault):
    monkeypatch.setattr(shared_alarm_selection.time, 'sleep', lambda _: None)
    session = SelectionSession(fault)
    with shared_control_reads.publish(ENROLLMENT.camera_id, session):
        with pytest.raises((ControlNotReady, ControlOperationError)):
            controls.write(camera, 'alarm_voice', ITEM.key)
    assert session.writes == [ITEM]


@pytest.mark.parametrize('value', [1, True, '', 'raw-vendor-id', 'system-99'])
def test_invalid_or_absent_options_do_not_write(camera, catalogue, value):
    session = SelectionSession()
    with shared_control_reads.publish(ENROLLMENT.camera_id, session):
        with pytest.raises(ControlOperationError): controls.write(camera, 'alarm_voice', value)
    assert not session.writes


def test_managed_path_uses_certified_options_and_rechecks_before_submission(camera, monkeypatch):
    monkeypatch.setattr(alarm_resource_controls, 'managed', lambda _: True)
    checked = []
    monkeypatch.setattr(alarm_resource_controls, '_fresh_resources', lambda entry, **kw:
        (ITEM,) if kw == {'native_only':True} else pytest.fail('missing native guard'))
    def revoked(entry, item):
        checked.append(item)
        raise ControlOperationError('revoked')
    monkeypatch.setattr(shared_alarm_selection, '_certified', revoked)
    session = SelectionSession()
    with shared_control_reads.publish(ENROLLMENT.camera_id, session):
        with pytest.raises(ControlOperationError, match='revoked'):
            controls.write(camera, 'alarm_voice', ITEM.key)
    assert checked == [ITEM] and not session.writes


def test_recovering_owner_cannot_open_resource_catalogue(camera, monkeypatch):
    monkeypatch.setattr(shared_alarm_catalog, 'read', lambda _: pytest.fail('read before ready'))
    with shared_control_reads.reserve_native_owner(ENROLLMENT.camera_id):
        with pytest.raises(ControlNotReady): controls.write(camera, 'alarm_voice', ITEM.key)


def test_typed_resource_job_writes_only_the_selection_leaf_once():
    from backend.app.drivers.yoosee.p2p.crypto import gute_mode2_decrypt
    from tests.test_lamp_read_job import NODE
    job, seq, _ = fixture(AlarmVoiceWriteSpec(7000000002, ITEM))
    plain = gute_mode2_decrypt(job.next_packet(), NODE.session_key)
    assert b'ProWritable.resFile.setVal.resId\0' in plain
    assert ITEM.resource_id.encode() in plain
    assert b'expelCtrl' not in plain and b'HTTP_PROXY' not in plain
    assert job.next_packet() is None
    job.observe(response(job), NODE.address, generation=1)
    assert job.result.status == 'response' and job.result.error_code == 0
    assert seq.allocate() == 41


@pytest.mark.parametrize('value', ['raw-vendor-id', {}, 7, None])
def test_raw_vendor_resource_not_admitted(value):
    with pytest.raises(ValueError): AlarmVoiceWriteSpec(7000000002, value)
