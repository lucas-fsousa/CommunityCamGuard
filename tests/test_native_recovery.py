"""Driver result classification only, without authentication, media or actions."""

from dataclasses import replace
from types import SimpleNamespace

import pytest

from backend.app.drivers.yoosee.native_recovery import retryable
from backend.app.drivers.yoosee.native_video_source import NativeVideoResult, NativeVideoSource
from backend.app.drivers.yoosee.p2p.account_read_worker import WorkerSummary
from backend.app.drivers.yoosee.shared_read_session import SharedReadResult


@pytest.mark.parametrize("reason,allowed", [
    ("socket",True),("media_complete",True),("contended",True),("stopped",True),
    ("protocol",False),("consumer_failed",False),("enrollment_changed",False),
    ("registry_unavailable",False),("unknown",False),
])
def test_only_explicit_driver_reasons_are_retried(reason, allowed):
    source = NativeVideoSource("test", "123")
    assert retryable(NativeVideoResult(reason, SharedReadResult(reason)), source) is allowed


@pytest.mark.parametrize("label,allowed", [
    ("offline",True),("socket_timeout",True),("socket_error",True),
    ("list_dns",True),("list_timeout",True),("stale_access",False),
    ("access_rejected",False),("transport_or_auth",False),
])
def test_authentication_rejection_does_not_trigger_renewal_or_retry(label, allowed):
    source = NativeVideoSource("test", "123")
    source._session._connection_failure = label
    assert retryable(NativeVideoResult("connection", SharedReadResult("connection")), source) is allowed


@pytest.mark.parametrize("fault", ["broker", "tcp_receipt", "tcp_socket", "motion", "media"])
def test_unconfirmed_cleanup_cannot_be_classified_retryable(fault):
    worker = WorkerSummary("socket",0,0,0,False)
    if fault == "broker": worker = replace(worker, socket_close_failed=True)
    if fault.startswith("tcp"):
        worker = replace(worker, tcp=SimpleNamespace(close_acknowledged=fault != "tcp_receipt",
                                                     socket_close_failed=fault == "tcp_socket"))
    if fault == "motion":
        worker = replace(worker, motion=SimpleNamespace(start_attempted=True, release_delivery_confirmed=False))
    if fault == "media":
        worker = replace(worker, media=SimpleNamespace(opened=True, broker_release=False))
    source = NativeVideoSource("test","123")
    assert not retryable(NativeVideoResult("socket", SharedReadResult("socket",worker)),source)
