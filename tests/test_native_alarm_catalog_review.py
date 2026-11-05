from types import SimpleNamespace

import pytest

from backend.app.diagnostics import yoosee_alarm_catalog as review
from backend.app.drivers.contracts import ControlNotReady
from backend.app.drivers.yoosee.p2p.alarm_voice_catalog import P2PAlarmVoiceCatalog
from tests.test_shared_control_reads import ENROLLMENT
from tests.test_yoosee_alarm_resource_controls import ITEM


@pytest.fixture
def setup(monkeypatch):
    monkeypatch.setattr(review.registry, 'get_camera_by_id', lambda _: object())
    monkeypatch.setattr(review.p2p, 'get_enrollment_for_camera', lambda _: ENROLLMENT)
    session = object()
    monkeypatch.setattr(review, 'session_for', lambda _: session)
    monkeypatch.setattr(review, '_state', lambda _: SimpleNamespace(resource_id=ITEM.resource_id))
    calls = []
    def read(entry):
        calls.append(entry)
        return P2PAlarmVoiceCatalog(entry.device_id, 1, 0, True, (ITEM,))
    monkeypatch.setattr(review, 'read', read)
    return calls


def test_review_returns_sanitized_options_and_digests_without_granting_controls(setup):
    result = review.review_native_catalog(camera_id=ENROLLMENT.camera_id, device_id=ENROLLMENT.device_id)
    assert result['system_total'] == 1 and result['custom_total'] == 0
    assert result['selected_key'] == ITEM.key
    assert result['resources'][0]['key'] == ITEM.key
    assert len(result['resources'][0]['identity_digest']) == 64
    assert ITEM.resource_id not in repr(result) and setup == [ENROLLMENT]


def test_missing_native_owner_never_opens_a_standalone_session(setup, monkeypatch):
    monkeypatch.setattr(review, 'read', lambda _: None)
    with pytest.raises(ControlNotReady):
        review.review_native_catalog(camera_id=ENROLLMENT.camera_id, device_id=ENROLLMENT.device_id)


def test_target_mismatch_stops_before_read(setup, monkeypatch):
    monkeypatch.setattr(review.p2p, 'get_enrollment_for_camera', lambda _: SimpleNamespace(camera_id='other',device_id=ENROLLMENT.device_id))
    with pytest.raises(ValueError):
        review.review_native_catalog(camera_id=ENROLLMENT.camera_id, device_id=ENROLLMENT.device_id)
    assert not setup


def test_unknown_resource_identity_never_guesses_current_option_from_slot(setup, monkeypatch):
    from tests.test_yoosee_alarm_resource_controls import resource
    monkeypatch.setattr(review, '_state', lambda _: SimpleNamespace(resource_id=resource(opaque=99).resource_id))
    result = review.review_native_catalog(camera_id=ENROLLMENT.camera_id, device_id=ENROLLMENT.device_id)
    assert result['selected_key'] is None


def test_owner_change_before_state_read_stops_review(setup, monkeypatch):
    sessions = iter((object(), object()))
    monkeypatch.setattr(review, 'session_for', lambda _: next(sessions))
    monkeypatch.setattr(review, '_state', lambda _: pytest.fail('state read on different owner'))
    with pytest.raises(ControlNotReady):
        review.review_native_catalog(camera_id=ENROLLMENT.camera_id, device_id=ENROLLMENT.device_id)
