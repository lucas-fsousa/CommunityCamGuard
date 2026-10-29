"""State queries reuse the current native owner; failures cannot open a fallback login."""

from dataclasses import replace
from types import SimpleNamespace

import pytest

from backend.app.drivers.contracts import ControlNotReady, ControlOperationError, WeeklySchedule
from backend.app.drivers.yoosee import controls
from backend.app.drivers.yoosee import shared_control_reads as module
from backend.app.drivers.yoosee.p2p.broker_mailbox import MailboxTicket, ReadOutcome
from backend.app.drivers.yoosee.p2p.lamp_read_inbox import LampReadTicket
from backend.app.drivers.yoosee.p2p.lamp_read_job import LampReadResult
from backend.app.drivers.yoosee.p2p.smart_protection_schedule import (
    native_smart_protection_schedule,
)
from tests.test_broker_mailbox import completion
from tests.test_read_preparation import ENROLLMENT


class Session:
    def __init__(self):
        self.ready = True
        self.paths = []
        self.cancelled = []
        self.result = ReadOutcome("response", replace(completion(), value={"setVal":5}, error_code=0))
    def matches_enrollment(self, entry): return self.ready and entry == ENROLLMENT
    def submit(self, path): self.paths.append(path); return MailboxTicket(1, 1)
    def take(self, ticket): return self.result
    def cancel(self, ticket): self.cancelled.append(ticket); return True
    def submit_lamp_read(self): self.paths.append("lamp:type12"); return LampReadTicket(1, 1)
    def take_lamp_read(self, ticket): return LampReadResult("response", False, True)
    def cancel_lamp_read(self, ticket): self.cancelled.append(ticket)


def test_no_native_owner_retains_legacy_dispatch():
    assert module.read(ENROLLMENT, "speaker_volume") is None


def test_volume_driver_reuses_current_account_without_legacy_login(monkeypatch):
    session = Session()
    monkeypatch.setattr(controls, "_enrollment", lambda _: ENROLLMENT)
    monkeypatch.setattr(controls, "run_with_fresh_access", lambda *a, **k: pytest.fail("second login"))
    with module.publish(ENROLLMENT.camera_id, session):
        result = controls.read(SimpleNamespace(camera_id=ENROLLMENT.camera_id), "speaker_volume")
    assert result.value == 50 and result.verified and result.authenticated
    assert result.application_acknowledged and not result.direct_connection
    assert session.paths == ["ProWritable.volume"] and len(session.cancelled) == 1
    assert module.read(ENROLLMENT, "speaker_volume") is None


@pytest.mark.parametrize("key", ["smart_protection", "smart_protection_schedule"])
def test_guard_state_and_schedule_reuse_same_read_only_root(monkeypatch, key):
    session = Session()
    schedule = WeeklySchedule("08:00", "18:00", ("mon", "wed"))
    value = {"setVal":{"enable":0, "plan":native_smart_protection_schedule(schedule)}}
    session.result = ReadOutcome("response", replace(completion(), value=value, error_code=0))
    monkeypatch.setattr(controls, "_enrollment", lambda _: ENROLLMENT)
    monkeypatch.setattr(controls, "run_with_fresh_access", lambda *a, **k: pytest.fail("second login"))
    with module.publish(ENROLLMENT.camera_id, session):
        result = controls.read(SimpleNamespace(camera_id=ENROLLMENT.camera_id), key)
    assert result.value == (False if key == "smart_protection" else schedule)
    assert result.verified and result.application_acknowledged
    assert session.paths == ["ProWritable.guardParm"] and result.native_previous_value is None


@pytest.mark.parametrize("key", ["smart_protection", "smart_protection_schedule"])
def test_malformed_guard_state_cannot_claim_success_or_open_fallback(monkeypatch, key):
    session = Session()
    monkeypatch.setattr(controls, "_enrollment", lambda _: ENROLLMENT)
    monkeypatch.setattr(controls, "run_with_fresh_access", lambda *a, **k: pytest.fail("fallback login"))
    with module.publish(ENROLLMENT.camera_id, session), pytest.raises(ControlOperationError):
        controls.read(SimpleNamespace(camera_id=ENROLLMENT.camera_id), key)


def test_shared_reads_never_accept_an_action_or_unmapped_control():
    with pytest.raises(ControlOperationError, match="not a supported"):
        module.read(ENROLLMENT, "siren_pulse")


@pytest.mark.parametrize("operation", ["write", "resource_options"])
def test_unintegrated_controls_cannot_preempt_live_native_stream(monkeypatch, operation):
    session = Session()
    monkeypatch.setattr(controls, "_enrollment", lambda _: ENROLLMENT)
    monkeypatch.setattr(controls, "run_with_fresh_access", lambda *a, **k: pytest.fail("parallel login"))
    camera = SimpleNamespace(camera_id=ENROLLMENT.camera_id)
    with module.publish(ENROLLMENT.camera_id, session), pytest.raises(ControlNotReady, match="cannot yet share"):
        if operation == "write": controls.write(camera, "white_light", True)
        else: controls.options(camera, "alarm_voice")
    assert not session.paths


def test_lamp_state_uses_typed_read_not_legacy_login_or_model_path(monkeypatch):
    session = Session()
    monkeypatch.setattr(controls, "_enrollment", lambda _: ENROLLMENT)
    monkeypatch.setattr(controls, "run_with_fresh_access", lambda *a, **k: pytest.fail("second login"))
    with module.publish(ENROLLMENT.camera_id, session):
        result = controls.read(SimpleNamespace(camera_id=ENROLLMENT.camera_id), "white_light")
    assert result.value is False and result.verified and result.application_acknowledged
    assert session.paths == ["lamp:type12"] and session.cancelled == [LampReadTicket(1, 1)]


@pytest.mark.parametrize("fault", ["unready", "changed_token", "transport", "bad_value", "camera_error"])
def test_active_owner_failure_is_not_absence_or_permission_to_authenticate(monkeypatch, fault):
    session = Session()
    entry = ENROLLMENT
    if fault == "unready": session.ready = False
    if fault == "changed_token": entry = replace(entry, access_token=b"changed")
    if fault == "transport": session.result = ReadOutcome("transport_rejected")
    if fault == "bad_value": session.result = ReadOutcome("response", replace(completion(), value={"setVal":99}))
    if fault == "camera_error": session.result = ReadOutcome("response", replace(completion(), error_code=7))
    monkeypatch.setattr(controls, "_enrollment", lambda _: entry)
    monkeypatch.setattr(controls, "run_with_fresh_access", lambda *a, **k: pytest.fail("fallback login"))
    with module.publish(ENROLLMENT.camera_id, session), pytest.raises((ControlNotReady, ControlOperationError)):
        controls.read(SimpleNamespace(camera_id=entry.camera_id), "speaker_volume")


def test_timeout_cancels_only_its_own_ticket(monkeypatch):
    session = Session()
    times = iter([0.0, 3.0])
    monkeypatch.setattr(module.time, "monotonic", lambda: next(times))
    with module.publish(ENROLLMENT.camera_id, session), pytest.raises(ControlOperationError, match="timed out"):
        module.read(ENROLLMENT, "speaker_volume")
    assert session.cancelled == [MailboxTicket(1, 1)]


def test_another_publication_cannot_replace_a_live_owner():
    session = Session()
    with module.publish(ENROLLMENT.camera_id, session):
        with pytest.raises(ControlNotReady), module.publish("another-camera", Session()):
            pytest.fail("second native owner admitted")
        assert module._sessions[ENROLLMENT.camera_id] is session
