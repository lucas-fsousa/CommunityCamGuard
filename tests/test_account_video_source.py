"""Lazy production bridge: no real account, network, camera or decoder."""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from backend.app.db.p2p import P2PEnrollment
from backend.app.drivers.yoosee.account_video_source import AccountVideoSource
from backend.app.drivers.yoosee.native_lease_recovery import retryable
from backend.app.drivers.yoosee.native_video_source import NativeVideoResult
from backend.app.drivers.yoosee.p2p.ptz_authority import PtzGrant
from backend.app.drivers.yoosee.shared_read_session import SharedReadResult
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
