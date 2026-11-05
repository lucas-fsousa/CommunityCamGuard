from dataclasses import replace
from types import SimpleNamespace

import pytest

from backend.app.drivers.contracts import ControlNotReady, ControlOperationError
from backend.app.drivers.yoosee import (
    alarm_resource_controls,
    controls,
    shared_alarm_catalog,
    shared_control_reads,
)
from backend.app.drivers.yoosee.p2p.alarm_voice import decode_alarm_voice_catalog
from backend.app.drivers.yoosee.p2p.catalog_read_job import CatalogReadResult
from tests.test_shared_control_reads import ENROLLMENT, Session
from tests.test_vendor_alarm_voice_catalog import _payload


class CatalogSession(Session):
    def __init__(self, fault=None):
        super().__init__()
        self.fault = fault
        self.parts = []

    def submit_catalog_read(self, system):
        self.parts.append(system)
        return system

    def take_catalog_read(self, ticket):
        if self.fault == 'generation': self.ready = False
        if self.fault == 'rejected': return CatalogReadResult('invalid_response')
        return CatalogReadResult('response', decode_alarm_voice_catalog(_payload(system=ticket, number=7)), True)

    def cancel_catalog_read(self, ticket):
        self.cancelled.append(ticket)


def test_no_owner_retains_legacy_path():
    assert shared_alarm_catalog.read(ENROLLMENT) is None


def test_options_reuse_same_owner_without_renewal_or_vendor_ids(monkeypatch):
    session = CatalogSession()
    monkeypatch.setattr(controls, '_enrollment', lambda _: ENROLLMENT)
    monkeypatch.setattr(alarm_resource_controls, 'managed', lambda _: False)
    monkeypatch.setattr(controls, 'run_with_fresh_access', lambda *a, **k: pytest.fail('parallel login'))
    with shared_control_reads.publish(ENROLLMENT.camera_id, session):
        result = controls.options(SimpleNamespace(camera_id=ENROLLMENT.camera_id), 'alarm_voice')
    assert [item.value for item in result] == ['system-7', 'custom-7']
    assert session.parts == session.cancelled == [True, False]
    assert 'resId' not in repr(result) and 'secret.invalid' not in repr(result)


@pytest.mark.parametrize('fault', ['generation', 'rejected'])
def test_failure_never_falls_back_or_reads_second_part(fault):
    session = CatalogSession(fault)
    with shared_control_reads.publish(ENROLLMENT.camera_id, session):
        with pytest.raises((ControlNotReady, ControlOperationError)):
            shared_alarm_catalog.read(ENROLLMENT)
    assert session.parts == session.cancelled == [True]


def test_recovering_and_changed_enrollment_never_use_another_account():
    with shared_control_reads.reserve_native_owner(ENROLLMENT.camera_id):
        with pytest.raises(ControlNotReady): shared_alarm_catalog.read(ENROLLMENT)
    with shared_control_reads.publish(ENROLLMENT.camera_id, CatalogSession()):
        with pytest.raises(ControlNotReady):
            shared_alarm_catalog.read(replace(ENROLLMENT, access_id=ENROLLMENT.access_id+1))
