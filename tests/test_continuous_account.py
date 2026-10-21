"""Virtual elapsed time and encrypted synthetic peers; no camera/network I/O."""

import pytest

from backend.app.drivers.yoosee.p2p.account_output_budget import (
    AccountOutputBudget,
    OutputExhausted,
)
from backend.app.drivers.yoosee.p2p.account_policy import AccountPolicy, TrafficBudget
from backend.app.drivers.yoosee.p2p.account_read_lifecycle import AccountReadLifecycle
from backend.app.drivers.yoosee.p2p.account_read_worker import AccountReadWorker
from tests.test_account_read_lifecycle import connection
from tests.test_account_read_worker import WorkerSocket
from tests.test_account_worker_ptz import SPEC, MotionSocket
from tests.test_broker_reads import DEVICE, NODE, PATH


def test_continuous_worker_survives_old_time_and_cumulative_limits(monkeypatch):
    sock = WorkerSocket()
    owner = AccountReadWorker(NODE, 1, "192.0.2.1", 1234, clock=lambda: sock.now,
                              policy=AccountPolicy.CONTINUOUS)
    original = sock.recvfrom
    tickets, completed = [], []
    next_read = 0.0
    def receive(size):
        nonlocal next_read
        if tickets:
            result = owner.mailbox.take(tickets[0])
            if result is not None:
                assert result.status == "response"
                completed.append(tickets.pop())
        if sock.now >= next_read and not tickets:
            tickets.append(owner.mailbox.submit(DEVICE.device_id, PATH))
            next_read = sock.now + 1
        return original(size)
    monkeypatch.setattr(sock, "recvfrom", receive)
    result = owner.run(sock, cancelled=lambda: sock.now >= 1800)
    assert result.reason == "stopped" and sock.closed == 1
    assert len(completed) > 1500 and result.sent_datagrams > 3000
    assert result.heartbeat_responses >= 35
    assert not owner._dispatcher._routes and owner._node is None
    assert len({t.generation for t in completed}) == 1


@pytest.mark.parametrize("fault,reason", [("heartbeat_silent", "heartbeat_timeout"),
                                         ("receive", "socket")])
def test_continuous_failure_does_not_reauthenticate(fault, reason):
    sock = WorkerSocket(fault)
    calls = []
    lifecycle = AccountReadLifecycle(clock=lambda: sock.now, policy=AccountPolicy.CONTINUOUS)
    def connect(generation):
        calls.append(generation)
        return connection(sock)
    result = lifecycle.run(connect)
    assert result.reason == reason and sock.closed == 1 and calls == [1]
    assert sock.now < 53


def test_token_bucket_replenishes_without_accumulating_idle_credit():
    now = [0.0]
    budget = TrafficBudget(2, 100, policy=AccountPolicy.CONTINUOUS, clock=lambda: now[0])
    budget.charge(50)
    budget.charge(50)
    with pytest.raises(ValueError): budget.charge(0)
    now[0] = 29
    with pytest.raises(ValueError): budget.charge(50)
    now[0] = 30
    budget.charge(50)
    now[0] = 10000
    budget.charge(50)
    budget.charge(50)
    with pytest.raises(ValueError): budget.charge(0)


def test_finite_budget_never_replenishes():
    now = [0.0]
    budget = TrafficBudget(1, 100, clock=lambda: now[0])
    budget.charge(100)
    now[0] = 10000
    with pytest.raises(ValueError): budget.charge(0)


def test_continuous_output_preserves_stop_reservation_across_refill():
    now = [0.0]
    budget = AccountOutputBudget(8, 8 * 4096, policy=AccountPolicy.CONTINUOUS,
                                 clock=lambda: now[0])
    reserve = budget.reserve_ptz()
    reserve.charge("start", 4096)
    for _ in range(2): budget.charge(4096)
    with pytest.raises(OutputExhausted): budget.charge(1)
    now[0] = 60
    for _ in range(3): budget.charge(4096)
    with pytest.raises(OutputExhausted): budget.charge(1)
    for _ in range(3): reserve.charge("release", 4096)
    for _ in range(2): reserve.charge("receipt", 4096)
    with pytest.raises(OutputExhausted): reserve.charge("start", 1)
    reserve.close()


def test_invalid_policy_cannot_silently_enable_continuous_mode():
    with pytest.raises(ValueError): AccountReadLifecycle(policy="continuous")


@pytest.mark.parametrize("invalid", [-1, float("nan"), float("inf")])
def test_invalid_clock_fails_closed(invalid):
    now = [0.0]
    budget = TrafficBudget(policy=AccountPolicy.CONTINUOUS, clock=lambda: now[0])
    now[0] = invalid
    with pytest.raises(ValueError): budget.charge(1)


def test_continuous_input_flood_still_closes_at_fixed_burst_limit(monkeypatch):
    sock = WorkerSocket()
    monkeypatch.setattr(sock, "recvfrom", lambda size: (b"", ("192.0.2.99", 1)))
    owner = AccountReadWorker(NODE, 1, "192.0.2.1", 1234, clock=lambda: sock.now,
                              policy=AccountPolicy.CONTINUOUS)
    result = owner.run(sock)
    assert result.reason == "protocol" and result.received_datagrams == 513
    assert sock.closed == 1 and not result.sent_datagrams


@pytest.mark.parametrize("silent", [False, True])
def test_continuous_cancel_still_drains_stop_and_blocks_unsafe_replacement(monkeypatch, silent):
    sock = MotionSocket("silent" if silent else None)
    lifecycle = AccountReadLifecycle(clock=lambda: sock.now, policy=AccountPolicy.CONTINUOUS)
    original = AccountReadWorker.run
    def run(self, transport, **kwargs):
        sock.on_start = self.stop
        return original(self, transport, initial_motion=SPEC, wait=sock.wait, **kwargs)
    monkeypatch.setattr(AccountReadWorker, "run", run)
    result = lifecycle.run(lambda _: connection(sock))
    assert result.reason == "stopped" and sock.closed == 1
    assert result.motion.release_delivery_confirmed is not silent
    assert lifecycle.state == ("blocked" if silent else "idle")
    assert len(sock.moves) == (4 if silent else 2)
    if silent:
        with pytest.raises(ValueError, match="cleanup unconfirmed"):
            lifecycle.run(lambda _: pytest.fail("must not reauthenticate"))


def test_byte_budget_is_independent_of_packet_allowance():
    now = [0.0]
    budget = TrafficBudget(policy=AccountPolicy.CONTINUOUS, clock=lambda: now[0])
    for _ in range(256): budget.charge(4096)
    with pytest.raises(ValueError): budget.charge(1)
    now[0] = 60
    budget.charge(4096)
