import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from backend.app.drivers.yoosee.p2p.account_read_lifecycle import (
    AccountReadLifecycle,
    ReadConnection,
)
from backend.app.drivers.yoosee.p2p.broker_read_socket import ReadSpec
from tests.test_account_read_worker import WorkerSocket
from tests.test_broker_reads import DEVICE, NODE, PATH


def connection(sock):
    return ReadConnection(NODE, sock, "192.0.2.1", 1234)


def submit(owner):
    return owner.submit(DEVICE.device_id, PATH)


def test_generations_reject_stale_tickets_without_replaying(monkeypatch):
    sock = WorkerSocket()
    owner = AccountReadLifecycle(clock=lambda: sock.now)
    tickets = []
    original = sock.recvfrom
    def receive(size):
        if not tickets:
            tickets.append(submit(owner))
        return original(size)
    monkeypatch.setattr(sock, "recvfrom", receive)
    assert owner.run(lambda generation: connection(sock)).reason == "deadline"
    assert owner.take(tickets[0]).status == "response"
    assert owner.state == "idle" and sock.closed == 1
    second = WorkerSocket()
    seen = []
    def connect(generation):
        seen.append(generation)
        assert sock.closed == 1
        assert owner.take(tickets[0]) is None
        assert not owner.cancel(tickets[0])
        owner.stop()
        return connection(second)
    assert owner.run(connect).reason == "stopped"
    assert seen == [2] and second.reads == 0 and second.closed == 1


@pytest.mark.parametrize("operation", ["drain", "stop"])
def test_during_connect_no_second_auth_and_cancellation_latches(operation):
    sock = WorkerSocket()
    owner = AccountReadLifecycle(clock=lambda: sock.now)
    entered, release = threading.Event(), threading.Event()
    def connect(generation):
        assert generation == 1
        entered.set()
        assert release.wait(2)
        return connection(sock)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(owner.run, connect)
        try:
            assert entered.wait(2)
            assert owner.state == "connecting"
            with pytest.raises(ValueError): submit(owner)
            with pytest.raises(ValueError): owner.run(lambda _: pytest.fail("second auth"))
            getattr(owner, operation)()
            assert owner.state == "draining"
            with pytest.raises(ValueError): submit(owner)
        finally:
            release.set()
        result = future.result(timeout=2)
    assert result.reason in ("stopped", "drained")
    assert sock.closed == 1 and sock.reads == sock.beats == 0
    assert owner.state == "idle"


def test_drain_rejects_queued_and_new_work_immediately(monkeypatch):
    sock = WorkerSocket()
    owner = AccountReadLifecycle(clock=lambda: sock.now)
    tickets = []
    original = sock.recvfrom
    def receive(size):
        if not tickets:
            tickets.append(submit(owner))
            owner.drain()
            owner.drain()
            with pytest.raises(ValueError): submit(owner)
            with pytest.raises(ValueError): owner.run(lambda _: pytest.fail("premature auth"))
        return original(size)
    monkeypatch.setattr(sock, "recvfrom", receive)
    assert owner.run(lambda _: connection(sock)).reason == "drained"
    assert owner.take(tickets[0]).status == "cancelled"
    assert sock.reads == 0 and sock.closed == 1


def test_close_failure_blocks_future_auth():
    sock = WorkerSocket("close")
    owner = AccountReadLifecycle(clock=lambda: sock.now)
    result = owner.run(lambda _: connection(sock))
    assert result.socket_close_failed and owner.state == "blocked"
    owner.stop()
    owner.drain()
    with pytest.raises(ValueError): owner.run(lambda _: pytest.fail("unsafe replacement"))


@pytest.mark.parametrize("error", [OSError, KeyboardInterrupt])
def test_failed_auth_is_not_automatically_retried(error):
    owner = AccountReadLifecycle()
    def connect(_): raise error("private credential")
    with pytest.raises(error): owner.run(connect)
    assert owner.state == "blocked"
    with pytest.raises(ValueError): owner.run(lambda _: pytest.fail("retry"))


def test_constructor_failure_closes_transferred_resource(monkeypatch):
    sock = WorkerSocket()
    owner = AccountReadLifecycle()
    def fail(*args, **kwargs): raise ValueError("construction")
    monkeypatch.setattr(
        "backend.app.drivers.yoosee.p2p.account_read_lifecycle.AccountReadWorker", fail)
    with pytest.raises(ValueError): owner.run(lambda _: connection(sock))
    assert sock.closed == 1 and owner.state == "blocked"


def test_interruption_during_worker_closes_but_blocks_uncertain_summary(monkeypatch):
    sock = WorkerSocket("interrupt")
    owner = AccountReadLifecycle(clock=lambda: sock.now)
    original = sock.recvfrom
    def receive(size):
        submit(owner)
        return original(size)
    monkeypatch.setattr(sock, "recvfrom", receive)
    with pytest.raises(KeyboardInterrupt): owner.run(lambda _: connection(sock))
    assert sock.closed == 1 and owner.state == "blocked"


def test_no_new_auth_while_physical_close_is_still_running(monkeypatch):
    sock = WorkerSocket()
    owner = AccountReadLifecycle(clock=lambda: sock.now)
    close = sock.close
    def closing():
        with pytest.raises(ValueError): owner.run(lambda _: pytest.fail("auth before close"))
        close()
    monkeypatch.setattr(sock, "close", closing)
    owner.run(lambda _: connection(sock))
    assert owner.state == "idle"


def test_connection_repr_does_not_expose_credentials_or_endpoints():
    assert repr(connection(WorkerSocket())) == "ReadConnection()"


def test_old_ticket_cannot_cancel_same_sequence_in_new_generation(monkeypatch):
    current = [WorkerSocket()]
    owner = AccountReadLifecycle(clock=lambda: current[0].now)
    tickets = []
    def execute():
        sock = current[0]
        original = sock.recvfrom
        issued = False
        def receive(size):
            nonlocal issued
            if not issued:
                issued = True
                ticket = submit(owner)
                if tickets:
                    assert ticket.sequence == tickets[0].sequence
                    assert ticket.generation != tickets[0].generation
                    assert not owner.cancel(tickets[0])
                    assert owner.take(tickets[0]) is None
                tickets.append(ticket)
            return original(size)
        monkeypatch.setattr(sock, "recvfrom", receive)
        return owner.run(lambda _: connection(sock))
    execute()
    # Leave the first result unread: retained results must not leak into generation2.
    current[0] = WorkerSocket()
    execute()
    assert owner.take(tickets[1]).status == "response"
    assert current[0].reads == 1


def test_drain_allows_already_sent_response_to_complete(monkeypatch):
    sock = WorkerSocket()
    owner = AccountReadLifecycle(clock=lambda: sock.now)
    tickets = []
    original = sock.recvfrom
    def receive(size):
        if not tickets:
            tickets.append(submit(owner))
        elif sock.reads:
            owner.drain()
        return original(size)
    monkeypatch.setattr(sock, "recvfrom", receive)
    assert owner.run(lambda _: connection(sock)).reason == "drained"
    assert owner.take(tickets[0]).status == "response"
    assert sock.reads == 1 and sock.beats == 0 and sock.closed == 1


def test_generation_exhaustion_never_calls_authentication():
    owner = AccountReadLifecycle()
    owner._generation = (1 << 64) - 1
    with pytest.raises(ValueError, match="exhausted"):
        owner.run(lambda _: pytest.fail("generation wraparound"))
    assert owner.state == "blocked"


def test_batch_results_are_detached_before_next_generation():
    sock = WorkerSocket()
    owner = AccountReadLifecycle(clock=lambda: sock.now)
    batch = owner.run_reads(lambda _: connection(sock), (ReadSpec(DEVICE.device_id, PATH),))
    assert batch.outcomes[0].status == "response"
    assert "completion" not in repr(batch)
    second = WorkerSocket()
    owner.run_reads(lambda _: connection(second), (ReadSpec(DEVICE.device_id, PATH),),
                    cancelled=lambda: True)
    assert batch.outcomes[0].status == "response" and second.closed == 1


@pytest.mark.parametrize("reads", [(), (None,), (ReadSpec(1, "Action.expelCtrl"),),
    (ReadSpec(DEVICE.device_id, PATH, -1),), (ReadSpec(DEVICE.device_id, PATH),) * 9])
def test_invalid_bootstrap_batch_never_authenticates(reads):
    owner = AccountReadLifecycle()
    with pytest.raises(ValueError):
        owner.run_reads(lambda _: pytest.fail("invalid batch authenticated"), reads)
    assert owner.state == "idle"
