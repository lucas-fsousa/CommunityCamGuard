"""Lazy production bridge: no real account, network, camera or decoder."""

import sqlite3
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from backend.app.db.p2p import P2PEnrollment
from backend.app.drivers.contracts import ControlNotReady
from backend.app.drivers.yoosee.account_video_source import AccountVideoSource
from backend.app.drivers.yoosee.native_account import NativeAccount, NativeBindingUnavailable
from backend.app.drivers.yoosee.native_lease_recovery import retryable
from backend.app.drivers.yoosee.native_video_source import NativeVideoResult
from backend.app.drivers.yoosee.p2p.ptz_authority import PtzGrant
from backend.app.drivers.yoosee.shared_read_session import SharedReadResult
from backend.app.media import recovering_source
from backend.app.media.mp4_fanout import StreamClosedError


@pytest.fixture
def setup_source():
    entry = P2PEnrollment("123456", 123, b"x" * 64, None, "", "", "cam_" + "a" * 24)
    account = SimpleNamespace(ready=True, cleanup_confirmed=False)
    accounts = Mock()
    accounts.prepare.return_value = account
    child = Mock()
    child.run.return_value = NativeVideoResult("stopped", SharedReadResult("stopped"))
    factory = Mock(return_value=child)
    valid = Mock(return_value=True)
    source = AccountVideoSource(entry, accounts, factory=factory, valid=valid)
    return source, entry, account, accounts, factory, child, valid


def test_construction_and_subscription_are_lazy_and_run_binds_the_exact_entry(setup_source):
    source, entry, _, accounts, factory, child, _ = setup_source
    accounts.prepare.assert_not_called()
    factory.assert_not_called()
    assert not source.recording
    with pytest.raises(StreamClosedError):
        source.subscribe()
    accounts.prepare.assert_not_called()
    def cancelled():
        return False
    assert source.run(cancelled=cancelled) is child.run.return_value
    accounts.prepare.assert_called_once_with((entry,), motion_grants=(), motion_valid=None)
    accounts.binding.assert_called_once_with(entry)
    factory.assert_called_once_with(accounts.binding.return_value)
    child.run.assert_called_once_with(cancelled=cancelled)
    with pytest.raises(ValueError, match="already started"):
        source.run(cancelled=cancelled)


@pytest.mark.parametrize("cancelled,valid,reason", [(True, True, "stopped"),
    (False, False, "enrollment_changed")])
def test_unadmitted_source_retires_without_login_or_consumer(setup_source, cancelled, valid, reason):
    source, _, _, accounts, factory, _, validator = setup_source
    validator.return_value = valid
    result = source.run(cancelled=lambda: cancelled)
    assert result.reason == reason
    assert source.diagnostics()["session_lease_detached"] is True
    accounts.prepare.assert_not_called()
    factory.assert_not_called()


@pytest.mark.parametrize("reason,failure,allowed", [("busy", "", True),
    ("account_ended", "", True),
    ("connection", "socket_timeout", True), ("connection", "credentials", False)])
def test_only_confirmed_preparation_failures_can_retry(setup_source, reason, failure, allowed):
    source, _, account, accounts, factory, _, _ = setup_source
    account.ready, account.cleanup_confirmed = False, True
    account.result_reason, account.connection_failure = reason, failure
    result = source.run(cancelled=lambda: False)
    assert result.reason == reason
    assert retryable(result, source) is allowed
    accounts.binding.assert_not_called()
    factory.assert_not_called()


def test_reviewed_motion_grant_and_validator_reach_the_retained_owner(setup_source):
    _, entry, _, accounts, factory, _, valid = setup_source
    grant = PtzGrant(entry.access_id, int(entry.device_id), frozenset({"right"}))
    source = AccountVideoSource(entry, accounts, factory=factory, valid=valid, motion_grants=(grant,))
    source.run(cancelled=lambda: False)
    kwargs = accounts.prepare.call_args.kwargs
    assert kwargs["motion_grants"] == (grant,) and kwargs["motion_valid"](entry.camera_id)
    valid.return_value = False
    assert not kwargs["motion_valid"](entry.camera_id)


def database_busy():
    error = sqlite3.OperationalError('PRIVATE_DATABASE_DETAIL')
    error.sqlite_errorcode = sqlite3.SQLITE_BUSY
    return error


def test_busy_validation_before_admission_never_logs_in(setup_source):
    source, _, _, accounts, factory, _, valid = setup_source
    valid.side_effect = database_busy()
    result = source.run(cancelled=lambda: False)
    assert result.reason == 'busy' and retryable(result, source)
    accounts.prepare.assert_not_called()
    factory.assert_not_called()


def test_account_draining_between_ready_and_binding_is_unadmitted_not_fatal(setup_source, monkeypatch):
    source, entry, _, accounts, factory, _, _ = setup_source
    account = NativeAccount((entry,), current_enrollment=lambda _: entry)
    account._inbox = Mock()  # No lease, socket or worker is created by this fixture.
    owns = Mock(side_effect=[True, False])
    monkeypatch.setattr(account._lifecycle, 'owns_media_leases', owns)
    accounts.prepare.return_value = account
    accounts.binding.side_effect = lambda selected: account.binding(selected.camera_id)
    result = source.run(cancelled=lambda: False)
    assert result.reason == 'busy' and retryable(result, source)
    assert source.current is None and source.diagnostics()['session_lease_detached'] is True
    assert not account.cleanup_confirmed  # Refused camera admission is not account cleanup.
    assert owns.call_count == 2
    factory.assert_not_called()


@pytest.mark.parametrize('stage', ['binding', 'factory', 'child'])
def test_only_typed_binding_refusal_is_retryable_not_unknown_or_later_errors(setup_source, stage):
    source, _, _, accounts, factory, child, _ = setup_source
    error = ControlNotReady('unknown binding refusal') if stage == 'binding' else NativeBindingUnavailable('late failure')
    target = {'binding': accounts.binding, 'factory': factory, 'child': child.run}[stage]
    target.side_effect = error
    with pytest.raises(type(error)):
        source.run(cancelled=lambda: False)


def test_changed_enrollment_is_not_a_temporary_binding_refusal(setup_source):
    _, entry, _, _, _, _, _ = setup_source
    account = NativeAccount((entry,), current_enrollment=lambda _: None)
    with pytest.raises(ControlNotReady) as result:
        account.binding(entry.camera_id)
    assert not isinstance(result.value, NativeBindingUnavailable)


def test_slow_account_preparation_can_resume_without_stopping_or_replacing_account(setup_source, monkeypatch):
    initial, entry, account, accounts, factory, child, valid = setup_source
    now, stopped = [0.0], [False]
    account.ready, account.stop = False, Mock()
    initial._clock = lambda: now[0]
    def sleep(duration):
        now[0] += duration
        if now[0] >= 23:
            account.ready = True
    monkeypatch.setattr(recovering_source.time, 'sleep', sleep)
    def finished(**kwargs):
        stopped[0] = True
        return NativeVideoResult('stopped', SharedReadResult('stopped'))
    child.run.side_effect = finished
    def replacement():
        assert now[0] >= 24 and initial.current is None
        assert not account.cleanup_confirmed  # Still the same independently owned account.
        return AccountVideoSource(entry, accounts, factory=factory, valid=valid, clock=lambda: now[0])
    source = recovering_source.RecoveringSource(initial, factory=replacement, valid=valid,
        retryable=retryable, clock=lambda: now[0])
    assert source.run(cancelled=lambda: stopped[0]).reason == 'stopped'
    assert source.restarts == 1 and accounts.prepare.call_count == 2
    accounts.binding.assert_called_once_with(entry)
    factory.assert_called_once()
    account.stop.assert_not_called()


@pytest.mark.parametrize('reason', ['stopped', 'enrollment_changed'])
def test_pending_account_preparation_still_observes_cancel_and_revocation(setup_source, monkeypatch, reason):
    source, _, account, accounts, factory, _, valid = setup_source
    now = [0.0]
    account.ready = False
    source._clock = lambda: now[0]
    def sleep(duration):
        now[0] += duration
    monkeypatch.setattr(recovering_source.time, 'sleep', sleep)
    if reason == 'enrollment_changed':
        valid.side_effect = lambda: now[0] < 1
    result = source.run(cancelled=lambda: reason == 'stopped' and now[0] >= 1)
    assert result.reason == reason and now[0] < 2
    assert source.current is None and not account.cleanup_confirmed
    accounts.binding.assert_not_called()
    factory.assert_not_called()


@pytest.mark.parametrize('fault', [None, 'detach', 'consumer', 'decoder'])
def test_database_busy_after_child_run_requires_full_retirement(setup_source, fault):
    source, _, _, _, _, child, _ = setup_source
    child.run.side_effect = database_busy()
    child.diagnostics.return_value = {'session_scope': 'media_lease',
        'session_lease_detached': fault != 'detach', 'consumer_alive': fault == 'consumer',
        'cleanup_failed': fault == 'decoder'}
    if fault is not None:
        with pytest.raises(sqlite3.OperationalError):
            source.run(cancelled=lambda: False)
    else:
        result = source.run(cancelled=lambda: False)
        assert result.reason == 'busy' and retryable(result, source)
