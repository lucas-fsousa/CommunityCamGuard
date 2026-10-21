"""Actual continuous pump and legacy reservation, synthetic encrypted socket only."""

import threading
from dataclasses import replace

import pytest

from backend.app.drivers.yoosee import shared_read_session as module
from backend.app.drivers.yoosee.p2p import control_ownership
from backend.app.drivers.yoosee.p2p.account_read_connect import ReadConnectFailure
from tests.test_account_read_lifecycle import connection
from tests.test_account_read_worker import WorkerSocket
from tests.test_broker_reads import PATH
from tests.test_read_preparation import ENROLLMENT
from tests.test_read_preparation import owner as owner


@pytest.fixture
def setup(monkeypatch, owner):
    sock = WorkerSocket()
    calls = []
    monkeypatch.setattr(module.p2p, "read_registered_enrollment", lambda _: ENROLLMENT)
    def opening(entry, **kwargs):
        assert owner._paused
        assert entry == ENROLLMENT and not kwargs["cancelled"]()
        calls.append("auth")
        return connection(sock)
    monkeypatch.setattr(module, "open_read_connection", opening)
    session = module.SharedReadSession(ENROLLMENT.camera_id, ENROLLMENT.device_id,
                                       clock=lambda: sock.now)
    return session, sock, calls


def test_continuous_read_after_old_deadline_and_server_shutdown(setup, monkeypatch, owner):
    session, sock, calls = setup
    original = sock.recvfrom
    tickets = []
    def receive(size):
        if sock.now >= 125 and not tickets:
            tickets.append(session.submit(PATH))
        return original(size)
    monkeypatch.setattr(sock, "recvfrom", receive)
    result = session.run(cancelled=lambda: sock.now >= 180)
    assert result.reason == "stopped" and result.worker.heartbeat_responses == 3
    assert session.take(tickets[0]).status == "response"
    assert calls == ["auth"] and sock.closed == 1 and not owner._paused
    with pytest.raises(ValueError, match="already started"):
        session.run(cancelled=lambda: False)
    assert calls == ["auth"]


@pytest.mark.parametrize("change", ["token", "device", "removed", "camera", "database"])
def test_changed_enrollment_or_database_failure_closes_without_new_login(setup, monkeypatch, change):
    session, sock, calls = setup
    reads = []
    def get(_):
        reads.append(sock.now)
        if sock.now < 70: return ENROLLMENT
        if change == "database": raise OSError("private database error")
        if change in ("removed", "camera"): return None
        if change == "token": return replace(ENROLLMENT, access_token=b"replacement")
        if change == "device": return replace(ENROLLMENT, device_id="987654321")
        return ENROLLMENT
    monkeypatch.setattr(module.p2p, "read_registered_enrollment", get)
    result = session.run(cancelled=lambda: sock.now >= 80)
    assert result.reason == ("registry_unavailable" if change == "database" else "enrollment_changed")
    assert 70 <= sock.now < 72 and len(reads) < 75
    assert calls == ["auth"] and sock.closed == 1
    assert not control_ownership._cleanup_uncertain
    assert "private" not in repr(result)


def test_explicit_stop_before_start_never_authenticates(setup):
    session, sock, calls = setup
    session.stop()
    assert session.run(cancelled=lambda: False).reason == "stopped"
    assert not calls and not sock.closed


@pytest.mark.parametrize("fault", ["safe", "unknown", "close"])
def test_cleanup_barrier_is_preserved(setup, monkeypatch, fault):
    session, sock, _calls = setup
    if fault == "close": sock.fault = "close"
    else:
        def fail(*args, **kwargs):
            if fault == "safe": raise ReadConnectFailure("confirmed closed")
            raise OSError("unknown cleanup")
        monkeypatch.setattr(module, "open_read_connection", fail)
    if fault == "safe":
        assert session.run(cancelled=lambda: False).reason == "connection"
        assert not control_ownership._cleanup_uncertain
    else:
        with pytest.raises((OSError, module.P2PProbeError)):
            session.run(cancelled=lambda: sock.now >= 1)
        assert control_ownership._cleanup_uncertain


def test_foreground_waiter_enters_only_after_physical_close(setup, monkeypatch, owner):
    session, sock, calls = setup
    waiting, entered = threading.Event(), threading.Event()
    original_lock = control_ownership._channel
    main_thread = threading.get_ident()
    class Lock:
        def acquire(self, **kwargs):
            if threading.get_ident() != main_thread: waiting.set()
            return original_lock.acquire(**kwargs)
        def release(self): original_lock.release()
    monkeypatch.setattr(control_ownership, "_channel", Lock())
    monkeypatch.setattr(control_ownership, "WAIT_SECONDS", 2)
    def foreground():
        with control_ownership.own_control_channel():
            assert sock.closed == 1 and not owner._paused
            entered.set()
    thread = threading.Thread(target=foreground)
    original_receive = sock.recvfrom
    def receive(size):
        if thread.ident is None and sock.now >= 125:
            thread.start()
            assert waiting.wait(1)
        return original_receive(size)
    monkeypatch.setattr(sock, "recvfrom", receive)
    try:
        result = session.run(cancelled=lambda: sock.now >= 180)
        assert result.reason == "contended"
    finally:
        if thread.ident is not None: thread.join(timeout=3)
    assert not thread.is_alive() and entered.is_set() and calls == ["auth"]


def test_busy_account_skips_instead_of_queuing_or_restarting(setup):
    session, sock, calls = setup
    results = []
    with control_ownership.own_control_channel():
        thread = threading.Thread(target=lambda: results.append(session.run(cancelled=lambda: False)))
        thread.start()
        thread.join(timeout=1)
        assert not thread.is_alive()
        assert not control_ownership.control_channel_contended()
    assert results[0].reason == "busy" and not calls and not sock.closed


def test_stop_during_authentication_reaches_factory_without_poison(setup, monkeypatch):
    session, _sock, _calls = setup
    def opening(*args, **kwargs):
        session.stop()
        assert kwargs["cancelled"]()
        raise ReadConnectFailure("opening cancelled with confirmed cleanup")
    monkeypatch.setattr(module, "open_read_connection", opening)
    assert session.run(cancelled=lambda: False).reason == "stopped"
    assert not control_ownership._cleanup_uncertain


@pytest.mark.parametrize("fault", ["missing", "mismatch", "database"])
def test_invalid_initial_registry_never_authenticates(setup, monkeypatch, fault):
    session, _sock, calls = setup
    def get(_):
        if fault == "database": raise OSError("private")
        if fault == "mismatch": return replace(ENROLLMENT, device_id="987654321")
        return None
    monkeypatch.setattr(module.p2p, "read_registered_enrollment", get)
    result = session.run(cancelled=lambda: False)
    assert result.reason == ("registry_unavailable" if fault == "database" else "enrollment_changed")
    assert not calls and not control_ownership._cleanup_uncertain
