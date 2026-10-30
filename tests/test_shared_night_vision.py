from dataclasses import replace

import pytest

from backend.app.drivers.contracts import ControlNotReady, ControlOperationError
from backend.app.drivers.yoosee import controls, shared_control_reads, shared_control_write
from backend.app.drivers.yoosee.p2p.night_vision import NIGHT_VISION_VALUES
from tests.test_shared_volume import ENROLLMENT, VolumeSession
from tests.test_shared_volume import camera as camera


class NightSession(VolumeSession):
    def __init__(self, fault=None, initial='automatic'):
        super().__init__(fault)
        self.result = replace(self.result, completion=replace(self.result.completion,
            value={'setVal': {'nightViewMode': NIGHT_VISION_VALUES[initial], 'multiFlip': 3}}))

    def submit_night_vision_write(self, mode):
        self.writes.append(mode)
        if self.fault != 'readback':
            self.result = replace(self.result, completion=replace(self.result.completion,
                value={'setVal': {'nightViewMode': NIGHT_VISION_VALUES[mode], 'multiFlip': 3}}))
        return object()


@pytest.mark.parametrize('mode', ['daytime', 'night'])
def test_night_mode_reuses_owner_and_requires_exact_readback(camera, mode):
    session = NightSession()
    with shared_control_reads.publish(ENROLLMENT.camera_id, session):
        result = controls.write(camera, 'night_vision', mode)
    assert result.verified and result.changed and result.application_acknowledged
    assert result.previous_value == 'automatic' and result.value == mode
    assert result.native_requested_value == NIGHT_VISION_VALUES[mode]
    assert session.writes == [mode]
    assert session.paths == ['ProWritable.videoParm'] * 2
    assert session.result.completion.value['setVal']['multiFlip'] == 3


@pytest.mark.parametrize('mode', list(NIGHT_VISION_VALUES))
def test_unchanged_mode_is_read_only(camera, mode):
    session = NightSession(initial=mode)
    with shared_control_reads.publish(ENROLLMENT.camera_id, session):
        result = controls.write(camera, 'night_vision', mode)
    assert result.verified and not result.changed and session.writes == []


@pytest.mark.parametrize('fault', ['rejected', 'readback', 'generation'])
def test_unconfirmed_night_mode_never_replays_or_logs_in_again(camera, fault, monkeypatch):
    monkeypatch.setattr(shared_control_write.time, 'sleep', lambda _: None)
    session = NightSession(fault)
    with shared_control_reads.publish(ENROLLMENT.camera_id, session):
        with pytest.raises((ControlNotReady, ControlOperationError)):
            controls.write(camera, 'night_vision', 'night')
    assert session.writes == ['night']


def test_missing_legacy_field_cannot_write_even_when_other_video_fields_exist(camera):
    session = NightSession()
    session.result = replace(session.result, completion=replace(session.result.completion,
        value={'setVal': {'nightViewModeV2': 2, 'multiFlip': 3}}))
    with shared_control_reads.publish(ENROLLMENT.camera_id, session):
        with pytest.raises(ControlOperationError):
            controls.write(camera, 'night_vision', 'night')
    assert not session.writes


def test_recovering_owner_cannot_fall_back_to_legacy(camera):
    with shared_control_reads.reserve_native_owner(ENROLLMENT.camera_id):
        with pytest.raises(ControlNotReady):
            controls.write(camera, 'night_vision', 'night')


@pytest.mark.parametrize('mode', [True, 0, 'auto', 'day', 'ir'])
def test_invalid_mode_preserves_driver_error_contract_before_preflight(camera, mode):
    session = NightSession()
    with shared_control_reads.publish(ENROLLMENT.camera_id, session):
        with pytest.raises(ControlOperationError, match='automatic, daytime or night'):
            controls.write(camera, 'night_vision', mode)
    assert not session.paths and not session.writes
