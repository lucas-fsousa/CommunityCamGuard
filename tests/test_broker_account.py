import struct

import pytest

from backend.app.drivers.yoosee.p2p.broker_account import BrokerAccount
from backend.app.drivers.yoosee.p2p.crypto import gute_mode2_decrypt
from tests.test_broker_heartbeat_reply import reply
from tests.test_broker_reads import DEVICE, NODE, PATH, response


def account():
    now = [0.0]
    return BrokerAccount(NODE, 1, "192.0.2.1", 1234, clock=lambda: now[0]), now


def read(owner, timeout=2):
    return owner.submit_read(DEVICE.device_id, PATH, timeout)


def deliver(owner, frame):
    return owner.receive(frame, NODE.address, generation=1)


def test_user_capacity_cannot_starve_one_reserved_heartbeat_slot():
    owner, now = account()
    now[0] = 49
    requests = [read(owner) for _ in range(8)]
    with pytest.raises(ValueError, match="capacity"):
        read(owner)
    now[0] = 50
    events = owner.tick()
    assert len(events.outgoing) == 1 and not events.reads
    wire = gute_mode2_decrypt(events.outgoing[0], NODE.session_key)
    assert struct.unpack_from("<I", wire, 12)[0] == 25
    assert not owner.tick().outgoing
    events = deliver(owner, reply(sequence=25, hint=35))
    assert events.heartbeats[0].status == "response"
    for request in reversed(requests):
        assert deliver(owner, response(request.request)).reads[0].request == request.request
    now[0] = 84.9
    assert not owner.tick().outgoing
    now[0] = 85
    assert len(owner.tick().outgoing) == 1
    owner.retire()


def test_heartbeat_timeout_retires_and_cancels_readers_no_retry():
    owner, now = account()
    now[0] = 50
    beat = owner.tick()
    request = read(owner, timeout=10)
    now[0] = 52
    events = owner.tick()
    assert beat.outgoing and events.retired == "heartbeat_timeout"
    assert events.reads[0].request == request.request and events.reads[0].status == "cancelled"
    assert events.heartbeats[0].status == "timeout"
    assert not owner.tick().outgoing and not owner.tick().reads
    assert not deliver(owner, reply()).heartbeats
    with pytest.raises(ValueError): read(owner)


def test_graceful_drain_finishes_reads_without_new_work_or_heartbeat():
    owner, now = account()
    now[0] = 49
    request = read(owner)
    assert not owner.drain().retired and owner.state == "draining"
    with pytest.raises(ValueError): read(owner)
    now[0] = 50
    assert not owner.tick().outgoing
    assert deliver(owner, response(request.request)).reads[0].status == "response"
    assert owner.tick().retired == "drained"
    assert not owner.retire().reads


def test_repeated_drain_cannot_extend_deadline():
    owner, now = account()
    request = read(owner, timeout=10)
    owner.drain()
    now[0] = 1.9
    owner.drain()
    now[0] = 2
    events = owner.tick()
    assert events.retired == "drain_timeout"
    assert events.reads[0].request == request.request
    assert events.reads[0].status == "cancelled"


@pytest.mark.parametrize("fault", ["peer", "generation"])
def test_unrelated_response_does_not_deliver(fault):
    owner, _ = account()
    request = read(owner)
    events = owner.receive(response(request.request),
        ("192.0.2.99", 1234) if fault == "peer" else NODE.address,
        generation=2 if fault == "generation" else 1)
    assert not events.reads
    assert owner.retire().reads[0].request == request.request


def test_hard_lifetime_and_expired_results_are_explicit():
    owner, now = account()
    first = read(owner, timeout=1)
    now[0] = 1
    events = deliver(owner, response(first.request))
    assert events.reads[0].status == "timeout"
    now[0] = 119
    with pytest.raises(ValueError): read(owner, timeout=2)
    second = read(owner, timeout=1)
    now[0] = 120
    events = owner.tick()
    assert events.retired == "lifetime" and events.reads[0].request == second.request
    assert not events.outgoing


@pytest.mark.parametrize("frame,count", [(bytes(4097), 1), (bytes(4096), 257), (b"", 513)])
def test_input_memory_and_work_budgets(frame, count):
    owner, _ = account()
    request = read(owner)
    for _ in range(count): events = deliver(owner, frame)
    assert events.retired == "input_budget" and owner.state == "retired"
    assert events.reads[0].request == request.request


def test_prepare_failure_preserves_already_expired_results(monkeypatch):
    from backend.app.drivers.yoosee.p2p import broker_heartbeat
    owner, now = account()
    request = read(owner)
    def fail(*args): raise ValueError("private detail")
    monkeypatch.setattr(broker_heartbeat, "build_heartbeat", fail)
    now[0] = 50
    events = owner.tick()
    assert events.retired == "prepare" and events.reads[0].request == request.request
    assert events.reads[0].status == "timeout" and "private" not in repr(events)


def test_transport_failure_cancels_every_waiter_and_clears_node():
    owner, now = account()
    now[0] = 50
    owner.tick()
    request = read(owner)
    events = owner.retire("transport")
    assert events.reads[0].request == request.request and events.heartbeats[0].status == "cancelled"
    assert owner._node is None
    assert not owner.retire("transport").reads
