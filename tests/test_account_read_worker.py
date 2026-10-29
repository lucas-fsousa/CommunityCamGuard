import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from backend.app.drivers.yoosee.p2p.account_read_worker import AccountReadWorker
from backend.app.drivers.yoosee.p2p.broker_maintenance import BrokerMaintenance
from tests.test_broker_account_check import Socket
from tests.test_broker_reads import DEVICE, NODE, PATH


class WorkerSocket(Socket):
    def recvfrom(self, size):
        assert size == 4097
        return super().recvfrom(4096)


def worker(sock):
    return AccountReadWorker(NODE, 1, "192.0.2.1", 1234, clock=lambda: sock.now)


def submit(owner): return owner.mailbox.submit(DEVICE.device_id, PATH)


def test_one_socket_reads_and_heartbeat_then_bounded_close():
    sock = WorkerSocket()
    owner = worker(sock)
    tickets = [submit(owner) for _ in range(8)]
    result = owner.run(sock)
    assert result.reason == "deadline" and sock.closed == 1
    assert result.heartbeat_responses == 1 and not result.socket_close_failed
    assert sock.reads == 8 and sock.beats == 1
    assert all(owner.mailbox.take(t).status == "response" for t in tickets)
    assert not owner._dispatcher._routes and owner._node is None
    with pytest.raises(ValueError, match="closed"): submit(owner)


def test_new_caller_after_initial_response_uses_same_session(monkeypatch):
    sock = WorkerSocket()
    owner = worker(sock)
    first = submit(owner)
    later = []
    original = sock.recvfrom
    def receive(size):
        if not later:
            result = owner.mailbox.take(first)
            if result is not None:
                assert result.status == "response"
                later.append(submit(owner))
        return original(size)
    monkeypatch.setattr(sock, "recvfrom", receive)
    result = owner.run(sock)
    assert result.reason == "deadline" and sock.reads == 2
    assert owner.mailbox.take(later[0]).status == "response"


@pytest.mark.parametrize("fault,reason", [("send", "socket"), ("receive", "socket"),
    ("ack", "socket"), ("heartbeat_silent", "heartbeat_timeout")])
def test_faults_close_once_without_replay(fault, reason):
    sock = WorkerSocket(fault)
    owner = worker(sock)
    ticket = submit(owner)
    result = owner.run(sock)
    assert result.reason == reason and sock.closed == 1
    assert sock.reads <= 1 and sock.beats <= 1
    terminal = owner.mailbox.take(ticket)
    assert terminal is not None
    if fault == "ack": assert terminal.status == "response"
    assert "private" not in repr(result)


def test_stop_before_run_closes_and_cancels_without_network():
    sock = WorkerSocket()
    owner = worker(sock)
    ticket = submit(owner)
    owner.stop()
    result = owner.run(sock)
    assert result.reason == "stopped" and sock.closed == 1
    assert not result.sent_datagrams
    assert owner.mailbox.take(ticket).status == "cancelled"


def test_interruption_also_closes_and_releases_every_waiter():
    sock = WorkerSocket("interrupt")
    owner = worker(sock)
    ticket = submit(owner)
    with pytest.raises(KeyboardInterrupt): owner.run(sock)
    assert sock.closed == 1 and owner._node is None
    assert owner.mailbox.take(ticket).status == "cancelled"


def test_close_failure_is_explicit_even_on_normal_deadline():
    sock = WorkerSocket("close")
    result = worker(sock).run(sock)
    assert result.reason == "deadline" and result.socket_close_failed


def test_second_run_cannot_reuse_generation():
    sock = WorkerSocket()
    owner = worker(sock)
    owner.stop()
    owner.run(sock)
    second = WorkerSocket()
    result = owner.run(second)
    assert result.reason == "already_started" and second.closed == 1
    assert not second.reads and not second.beats


def test_oversized_input_is_detected_and_closes(monkeypatch):
    sock = WorkerSocket()
    monkeypatch.setattr(sock, "recvfrom", lambda size: (b"x" * 4097, NODE.address))
    result = worker(sock).run(sock)
    assert result.reason == "protocol" and result.received_datagrams == 1
    assert result.protocol_site and ':' in result.protocol_site
    assert 'xxxx' not in result.protocol_site
    assert sock.closed == 1


def test_input_flood_has_fixed_bound(monkeypatch):
    sock = WorkerSocket()
    monkeypatch.setattr(sock, "recvfrom", lambda size: (b"", ("192.0.2.99", 1)))
    result = worker(sock).run(sock)
    assert result.reason == "protocol" and result.received_datagrams == 513
    assert sock.closed == 1


@pytest.mark.parametrize('message,code',[
    ('conflicting KCP duplicate','kcp_conflicting_duplicate'),('private-value-do-not-log','')])
def test_protocol_diagnostics_use_known_codes_not_exception_payload(monkeypatch,message,code):
    sock=WorkerSocket()
    def fail(_): raise ValueError(message)
    monkeypatch.setattr(sock,'recvfrom',fail)
    result=worker(sock).run(sock)
    assert result.protocol_code==code and result.reason=='protocol'
    assert 'private-value' not in repr(result)


def test_drain_allows_inflight_response_but_rejects_queued_and_new_work(monkeypatch):
    sock = WorkerSocket()
    owner = worker(sock)
    active = submit(owner)
    queued = []
    original = sock.recvfrom
    def receive(size):
        if not queued:
            queued.append(submit(owner))
            owner.drain()
        return original(size)
    monkeypatch.setattr(sock, "recvfrom", receive)
    result = owner.run(sock)
    assert result.reason == "drained" and sock.reads == 1
    assert owner.mailbox.take(active).status == "response"
    assert owner.mailbox.take(queued[0]).status == "cancelled"
    with pytest.raises(ValueError, match="closed"): submit(owner)


def test_silent_drain_has_two_second_ceiling_not_extended_each_poll(monkeypatch):
    sock = WorkerSocket("read_silent")
    owner = worker(sock)
    ticket = owner.mailbox.submit(DEVICE.device_id, PATH, 10)
    original = sock.recvfrom
    def receive(size):
        owner.drain()
        return original(size)
    monkeypatch.setattr(sock, "recvfrom", receive)
    result = owner.run(sock)
    assert result.reason == "drain_timeout" and sock.now < 2.2
    assert owner.mailbox.take(ticket).status == "cancelled"
    assert not sock.beats and sock.closed == 1


def test_reentrant_run_cannot_close_socket_of_active_owner(monkeypatch):
    sock = WorkerSocket()
    owner = worker(sock)
    original = sock.recvfrom
    def receive(size):
        duplicate = owner.run(sock)
        assert duplicate.reason == "already_started" and sock.closed == 0
        owner.stop()
        return original(size)
    monkeypatch.setattr(sock, "recvfrom", receive)
    assert owner.run(sock).reason == "stopped"
    assert sock.closed == 1


def test_concurrent_caller_admission_and_duplicate_worker_are_isolated(monkeypatch):
    sock = WorkerSocket()
    owner = worker(sock)
    first = submit(owner)
    entered, release = threading.Event(), threading.Event()
    original = sock.recvfrom
    def receive(size):
        if not entered.is_set():
            entered.set()
            assert release.wait(5)
        return original(size)
    monkeypatch.setattr(sock, "recvfrom", receive)
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(owner.run, sock)
        try:
            assert entered.wait(5)
            second = submit(owner)
            assert owner.mailbox.cancel(first)
            duplicate_sock = WorkerSocket()
            assert owner.run(duplicate_sock).reason == "already_started"
            assert duplicate_sock.closed == 1 and sock.closed == 0
        finally:
            release.set()
        assert pending.result(timeout=5).reason == "deadline"
    assert owner.mailbox.take(first).status == "cancelled"
    assert owner.mailbox.take(second).status == "response"
    assert sock.reads == 2 and sock.closed == 1


def test_output_flood_is_bounded_independently_of_input(monkeypatch):
    sock = WorkerSocket()
    monkeypatch.setattr(sock, "recvfrom", lambda size: (b"", NODE.address))
    monkeypatch.setattr(sock, "sendto", lambda wire, peer: len(wire))
    monkeypatch.setattr(BrokerMaintenance, "receive", lambda *args, **kwargs: (b"x",) * 3)
    result = worker(sock).run(sock)
    assert result.reason == "output_budget"
    assert result.sent_datagrams == 512 and result.received_datagrams == 171
    assert sock.closed == 1
