from dataclasses import replace

import pytest

from backend.app.drivers.yoosee import native_ptz
from backend.app.drivers.yoosee import read_preparation as preparation
from backend.app.drivers.yoosee.p2p import control_ownership
from backend.app.drivers.yoosee.p2p.account_read_connect import ReadConnectFailure
from backend.app.drivers.yoosee.p2p.account_read_lifecycle import ReadConnection
from backend.app.drivers.yoosee.p2p.ptz_cache import PtzRouteCache
from tests.test_account_read_worker import WorkerSocket
from tests.test_av_route import ENROLLMENT as BASE_ENROLLMENT
from tests.test_broker_reads import DEVICE, NODE
from tests.test_native_av_broker_ownership import available_from_other_thread

ENROLLMENT = replace(BASE_ENROLLMENT, device_id=str(DEVICE.device_id))


@pytest.fixture
def owner(monkeypatch):
    monkeypatch.setattr(control_ownership, "_cleanup_uncertain", False)
    monkeypatch.setattr(native_ptz, "_active", {})
    monkeypatch.setattr(native_ptz, "_warming", {})
    cache = PtzRouteCache(shared_account=True)
    monkeypatch.setattr(native_ptz, "_routes", cache)
    return cache


def run(sock, **kwargs):
    return preparation.prepare_identity_reads(ENROLLMENT, camera_id="cam_test", device_id=ENROLLMENT.device_id,
                                               clock=lambda: sock.now, **kwargs)


def connect(sock, monkeypatch, owner):
    calls = []
    def opening(*args, **kwargs):
        assert owner._paused and not available_from_other_thread()
        calls.append("auth")
        return ReadConnection(NODE, sock, "192.0.2.1", 1234)
    monkeypatch.setattr(preparation, "open_read_connection", opening)
    close = sock.close
    def closing():
        assert owner._paused and not available_from_other_thread()
        close()
    monkeypatch.setattr(sock, "close", closing)
    return calls


def test_real_worker_batch_and_close_stay_inside_legacy_reservation(owner, monkeypatch):
    sock = WorkerSocket()
    calls = connect(sock, monkeypatch, owner)
    result = run(sock)
    assert result.failure is None and result.responses == 3
    assert result.worker.heartbeat_responses == 1
    assert calls == ["auth"] and sock.closed == 1 and sock.reads == 3
    assert not owner._paused and not control_ownership._cleanup_uncertain
    assert available_from_other_thread()
    assert "192.0.2" not in repr(result)


@pytest.mark.parametrize("fault,failure", [("read_silent", "read_incomplete"),
    ("heartbeat_silent", "heartbeat_timeout"), ("receive", "socket"), ("send", "socket")])
def test_known_closed_read_failure_does_not_poison_ptz(owner, monkeypatch, fault, failure):
    sock = WorkerSocket(fault)
    calls = connect(sock, monkeypatch, owner)
    assert run(sock).failure == failure
    assert calls == ["auth"] and sock.closed == 1
    assert not control_ownership._cleanup_uncertain
    with control_ownership.own_control_channel(): pass


def test_unconfirmed_socket_close_blocks_legacy_auth(owner, monkeypatch):
    sock = WorkerSocket("close")
    connect(sock, monkeypatch, owner)
    with pytest.raises(preparation.P2PProbeError, match="cleanup"): run(sock)
    assert sock.closed == 1 and control_ownership._cleanup_uncertain
    with pytest.raises(control_ownership.ControlChannelBusy):
        with control_ownership.own_control_channel(): pytest.fail("new login")


def test_cancel_before_handoff_never_opens(owner, monkeypatch):
    monkeypatch.setattr(preparation, "open_read_connection", lambda *a, **k: pytest.fail("auth"))
    assert run(WorkerSocket(), cancelled=lambda: True).failure == "cancelled"
    assert not owner._paused and not control_ownership._cleanup_uncertain


def test_cancel_after_auth_closes_without_sending_or_poison(owner, monkeypatch):
    sock = WorkerSocket()
    connect(sock, monkeypatch, owner)
    checks = [0]
    def cancelled():
        checks[0] += 1
        return checks[0] >= 3
    assert run(sock, cancelled=cancelled).failure == "stopped"
    assert sock.reads == sock.beats == 0 and sock.closed == 1
    assert not control_ownership._cleanup_uncertain


def test_confirmed_factory_failure_does_not_poison_legacy(owner, monkeypatch):
    def opening(*args, **kwargs): raise ReadConnectFailure("safe failure")
    monkeypatch.setattr(preparation, "open_read_connection", opening)
    assert run(WorkerSocket()).failure == "connection"
    assert not control_ownership._cleanup_uncertain


@pytest.mark.parametrize("error", [OSError, KeyboardInterrupt])
def test_unknown_factory_failure_blocks_legacy(owner, monkeypatch, error):
    def opening(*args, **kwargs): raise error("private")
    monkeypatch.setattr(preparation, "open_read_connection", opening)
    with pytest.raises(error): run(WorkerSocket())
    assert control_ownership._cleanup_uncertain


def test_mismatched_target_fails_before_cache_retirement(owner, monkeypatch):
    monkeypatch.setattr(preparation, "open_read_connection", lambda *a, **k: pytest.fail("auth"))
    with pytest.raises(ValueError):
        preparation.prepare_identity_reads(ENROLLMENT, camera_id="other", device_id="123")
    assert not owner._paused and not control_ownership._cleanup_uncertain


def test_competing_control_before_auth_yields_without_login(owner, monkeypatch):
    monkeypatch.setattr(preparation, "control_channel_contended", lambda: True)
    monkeypatch.setattr(preparation, "open_read_connection", lambda *a, **k: pytest.fail("auth"))
    assert run(WorkerSocket()).failure == "cancelled"
    assert not owner._paused and not control_ownership._cleanup_uncertain


def test_competing_control_during_auth_reaches_factory_cancellation(owner, monkeypatch):
    demand = [False]
    monkeypatch.setattr(preparation, "control_channel_contended", lambda: demand[0])
    def opening(*args, **kwargs):
        assert not kwargs["cancelled"]()
        demand[0] = True
        assert kwargs["cancelled"]()
        raise ReadConnectFailure("cancelled with confirmed cleanup")
    monkeypatch.setattr(preparation, "open_read_connection", opening)
    assert run(WorkerSocket()).failure == "connection"
    assert not owner._paused and not control_ownership._cleanup_uncertain


@pytest.mark.parametrize("close_fails", [False, True])
def test_competing_control_stops_reads_but_never_bypasses_close(owner, monkeypatch, close_fails):
    sock = WorkerSocket("close" if close_fails else None)
    calls = connect(sock, monkeypatch, owner)
    demand = [False]
    monkeypatch.setattr(preparation, "control_channel_contended", lambda: demand[0])
    receive = sock.recvfrom
    def receiving(size):
        demand[0] = True
        return receive(size)
    monkeypatch.setattr(sock, "recvfrom", receiving)
    if close_fails:
        with pytest.raises(preparation.P2PProbeError, match="cleanup"): run(sock)
    else:
        assert run(sock).failure == "stopped"
    assert sock.now < 1 and sock.closed == 1 and calls == ["auth"]
    assert control_ownership._cleanup_uncertain == close_fails
