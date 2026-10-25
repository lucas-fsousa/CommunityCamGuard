"""State queries reuse the current native owner; failures cannot open a fallback login."""

from dataclasses import replace
from types import SimpleNamespace

import pytest

from backend.app.drivers.contracts import ControlNotReady, ControlOperationError
from backend.app.drivers.yoosee import controls
from backend.app.drivers.yoosee import shared_control_reads as module
from backend.app.drivers.yoosee.p2p.broker_mailbox import MailboxTicket, ReadOutcome
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


def test_no_native_owner_retains_legacy_dispatch():
    assert module.read_volume(ENROLLMENT) is None


def test_volume_driver_reuses_current_account_without_legacy_login(monkeypatch):
    session = Session()
    monkeypatch.setattr(controls, "_enrollment", lambda _: ENROLLMENT)
    monkeypatch.setattr(controls, "run_with_fresh_access", lambda *a, **k: pytest.fail("second login"))
    with module.publish(ENROLLMENT.camera_id, session):
        result = controls.read(SimpleNamespace(camera_id=ENROLLMENT.camera_id), "speaker_volume")
    assert result.value == 50 and result.verified and result.authenticated
    assert result.application_acknowledged and not result.direct_connection
    assert session.paths == ["ProWritable.volume"] and len(session.cancelled) == 1
    assert module.read_volume(ENROLLMENT) is None


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
        module.read_volume(ENROLLMENT)
    assert session.cancelled == [MailboxTicket(1, 1)]


def test_another_publication_cannot_replace_a_live_owner():
    session = Session()
    with module.publish(ENROLLMENT.camera_id, session):
        with pytest.raises(ControlNotReady), module.publish("another-camera", Session()):
            pytest.fail("second native owner admitted")
        assert module._sessions[ENROLLMENT.camera_id] is session
