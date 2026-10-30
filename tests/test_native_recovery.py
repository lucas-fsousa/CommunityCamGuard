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
    ("transport",True),("heartbeat_timeout",True),("lifetime",True),("busy",True),
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


@pytest.mark.parametrize('fault',[None,'route','socket','motion','code'])
def test_timeout_reconnect_requires_closed_sockets_and_confirmed_outer_route(fault):
    worker=WorkerSummary('protocol',0,0,0,False,
        media=SimpleNamespace(opened=True,broker_release=fault!='route'),
        tcp=SimpleNamespace(ready=True,close_acknowledged=False,socket_close_failed=fault=='socket'),
        motion=SimpleNamespace(start_attempted=True,release_delivery_confirmed=fault!='motion'),
        protocol_code='bad_format' if fault=='code' else 'kcp_assembly_timeout')
    source=NativeVideoSource('test','123')
    assert retryable(NativeVideoResult('transport',SharedReadResult('transport',worker)),source) is (fault is None)


@pytest.mark.parametrize('failure_type', ['TimeoutError', 'Mp4SizeError'])
@pytest.mark.parametrize('fault', [None, 'broker', 'route', 'tcp', 'not_ready', 'motion', 'format', 'protocol'])
def test_decoder_stall_reconnect_requires_retirement_and_known_consumer_failure(fault, failure_type):
    worker = WorkerSummary('protocol' if fault == 'protocol' else 'stopped', 0, 0, 0, fault == 'broker',
        media=SimpleNamespace(opened=True, broker_release=fault != 'route'),
        tcp=SimpleNamespace(ready=fault != 'not_ready', close_acknowledged=False,
                            socket_close_failed=fault == 'tcp'),
        motion=SimpleNamespace(start_attempted=True, release_delivery_confirmed=fault != 'motion'))
    source = NativeVideoSource('test', '123')
    result = NativeVideoResult('consumer_failed', SharedReadResult('stopped', worker),
                               'ValueError' if fault == 'format' else failure_type)
    assert retryable(result, source) is (fault is None)


@pytest.mark.parametrize('code', ['tcp_session_timeout', 'av_handshake_timeout', 'av_control_timeout',
    'media_initialization_timeout', 'media_idle_timeout', 'tcp_io_timeout', 'tcp_eof'])
@pytest.mark.parametrize('fault', [None, 'route', 'socket', 'motor'])
def test_failed_handshake_can_retry_only_after_confirmed_retirement(code, fault):
    worker = WorkerSummary('protocol', 0, 0, 0, False,
        media=SimpleNamespace(opened=True, broker_release=fault != 'route'),
        tcp=SimpleNamespace(ready=False, close_acknowledged=False, socket_close_failed=fault == 'socket'),
        motion=SimpleNamespace(start_attempted=True, release_delivery_confirmed=fault != 'motor'),
        protocol_code=code)
    source = NativeVideoSource('test', '123')
    assert retryable(NativeVideoResult('transport', SharedReadResult('transport', worker)), source) is (fault is None)
