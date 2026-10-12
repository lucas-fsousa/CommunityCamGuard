import struct

import pytest

from backend.app.drivers.yoosee.p2p.broker_read_dispatcher import BrokerReadDispatcher
from backend.app.drivers.yoosee.p2p.crypto import gute_mode2_decrypt
from tests.test_broker_heartbeat_reply import reply
from tests.test_broker_reads import DEVICE, NODE, PATH, response


def dispatcher():
    now = [0.0]
    owner = BrokerReadDispatcher(NODE, 1, "192.0.2.1", 1234, clock=lambda: now[0])
    return owner, owner.mailbox, now


def submit(box, timeout=2): return box.submit(DEVICE.device_id, PATH, timeout)


def deliver(owner, frame): return owner.receive(frame, NODE.address, generation=1)


def test_identical_reads_completed_in_reverse_route_to_exact_callers():
    owner, box, _ = dispatcher()
    tickets = [submit(box) for _ in range(8)]
    batch = owner.step()
    assert len(batch.reads) == 8
    assert not owner.step().reads
    for item in reversed(batch.reads):
        assert box.ready(item.ticket)
        assert not deliver(owner, response(item.prepared.request)).reads
        result = box.take(item.ticket)
        assert result.completion.request == item.prepared.request
        assert result.status == "response"
    assert not owner._routes and not owner._requests
    assert all(box.take(t) is None for t in tickets)


def test_cancel_releases_owner_capacity_before_next_batch():
    owner, box, _ = dispatcher()
    tickets = [submit(box) for _ in range(8)]
    old = owner.step()
    for ticket in tickets:
        box.cancel(ticket)
        assert box.take(ticket).status == "cancelled"
        assert not box.ready(ticket)
    with pytest.raises(ValueError, match="capacity"): submit(box)
    owner.step()
    new = [submit(box) for _ in range(8)]
    current = owner.step()
    assert len(current.reads) == 8
    for item in old.reads: deliver(owner, response(item.prepared.request))
    assert all(box.take(t) is None for t in new)
    owner.close()
    assert all(box.take(t).status == "cancelled" for t in new)


def test_queue_delay_does_not_extend_read_deadline():
    owner, box, now = dispatcher()
    ticket = submit(box, 2)
    now[0] = 1.9
    item = owner.step().reads[0]
    now[0] = 2
    assert not box.ready(ticket)
    deliver(owner, response(item.prepared.request))
    owner.step()
    assert box.take(ticket).status == "timeout"
    assert not owner._routes and not owner._requests


def test_reserved_heartbeat_and_read_namespace_are_shared():
    owner, box, now = dispatcher()
    now[0] = 49
    tickets = [submit(box, 10) for _ in range(8)]
    batch = owner.step()
    now[0] = 50
    heartbeat = owner.step().maintenance
    wire = gute_mode2_decrypt(heartbeat.outgoing[0], NODE.session_key)
    sequence = struct.unpack_from("<I", wire, 12)[0]
    assert sequence not in [r.prepared.request.ticket.sequence for r in batch.reads]
    assert deliver(owner, reply(sequence=sequence)).heartbeats[0].status == "response"
    for item in batch.reads: deliver(owner, response(item.prepared.request))
    assert all(box.take(t).status == "response" for t in tickets)


def test_prepare_failure_cancels_prepared_and_queued_without_returning_wires(monkeypatch):
    owner, box, _ = dispatcher()
    original = owner._account.submit_read
    calls = []
    def fail_second(*args):
        calls.append(None)
        if len(calls) == 2: raise ValueError("synthetic")
        return original(*args)
    monkeypatch.setattr(owner._account, "submit_read", fail_second)
    tickets = [submit(box) for _ in range(3)]
    result = owner.step()
    assert not result.reads and not result.maintenance.outgoing
    assert result.maintenance.retired == "prepare"
    assert all(box.take(t).status == "cancelled" for t in tickets)
    assert not owner._routes and not owner._requests


def test_close_cancels_queued_and_active_and_preserves_completed():
    owner, box, _ = dispatcher()
    first, active = submit(box), submit(box)
    initial = owner.step()
    deliver(owner, response(initial.reads[0].prepared.request))
    queued = submit(box)
    owner.close()
    assert box.take(first).status == "response"
    assert box.take(active).status == box.take(queued).status == "cancelled"
    assert not owner.step().reads and not owner.close().reads
    with pytest.raises(ValueError, match="closed"): submit(box)


def test_heartbeat_timeout_retires_mailbox_and_all_routes():
    owner, box, now = dispatcher()
    now[0] = 50
    ticket = submit(box, 10)
    owner.step()
    now[0] = 52
    assert owner.step().maintenance.retired == "heartbeat_timeout"
    assert box.take(ticket).status == "cancelled"
    assert not owner._routes and not owner._requests


def test_cancel_during_preparation_releases_unsent_reservation(monkeypatch):
    owner, box, _ = dispatcher()
    ticket = submit(box)
    original = owner._account.submit_read
    def cancelling(*args):
        result = original(*args)
        box.cancel(ticket)
        return result
    monkeypatch.setattr(owner._account, "submit_read", cancelling)
    assert not owner.step().reads
    assert box.take(ticket).status == "cancelled"
    owner.step()
    assert not owner._routes and not owner._requests
    assert not owner._account._reads.pending_count


def test_wrong_peer_and_generation_cannot_complete_caller():
    owner, box, _ = dispatcher()
    ticket = submit(box)
    item = owner.step().reads[0]
    frame = response(item.prepared.request)
    owner.receive(frame, ("192.0.2.99", 99), generation=1)
    owner.receive(frame, NODE.address, generation=2)
    assert box.take(ticket) is None
    deliver(owner, frame)
    assert box.take(ticket).status == "response"
