import struct
from dataclasses import replace

import pytest

from backend.app.drivers.yoosee.p2p.broker_heartbeat import BrokerHeartbeat
from backend.app.drivers.yoosee.p2p.broker_sequences import BrokerSequences
from backend.app.drivers.yoosee.p2p.broker_transactions import ReceiptTicket
from backend.app.drivers.yoosee.p2p.crypto import gute_mode2_decrypt
from tests.test_broker_heartbeat_reply import reply
from tests.test_broker_model_reply import NODE
from tests.test_broker_transactions import ack


def owner(clock=lambda: 0):
    source = BrokerSequences(NODE.session_id, 1, 17)
    return BrokerHeartbeat(NODE.session_id, 1, source, clock=clock), source


def prepare(heartbeat, **kwargs):
    return heartbeat.prepare(NODE, "192.0.2.1", 1234, **kwargs)


def test_real_codec_uses_account_sequence_and_response_once():
    heartbeat, source = owner()
    assert source.allocate() == 17
    request = prepare(heartbeat)
    plain = gute_mode2_decrypt(request.wire, NODE.session_key)
    assert plain[1] == 0xA0 and struct.unpack_from("<I", plain, 12)[0] == 18
    assert "wire=" not in repr(request)
    assert heartbeat.accept(ack(18, 0xA0, NODE.session_id), generation=1) is None
    result = heartbeat.accept(reply(sequence=18, hint=35), generation=1)
    assert result.ticket == request.ticket and result.status == "response"
    assert heartbeat.cadence_seconds == 35
    assert heartbeat.accept(reply(sequence=18), generation=1) is None
    assert heartbeat.close() is None and source.allocate() == 19


@pytest.mark.parametrize("hint,cadence", [(0, 40), (9, 40), (10, 50), (25, 50), (35, 35), (60, 50), (61, 40)])
def test_interval_boundaries_preserve_previous_on_invalid(hint, cadence):
    heartbeat, _ = owner()
    prepare(heartbeat)
    heartbeat.accept(reply(hint=40), generation=1)
    prepare(heartbeat)
    heartbeat.accept(reply(sequence=18, hint=hint), generation=1)
    assert heartbeat.cadence_seconds == cadence


def test_timeout_pending_capacity_and_late_reply_are_explicit():
    now = [0.0]
    heartbeat, source = owner(lambda: now[0])
    request = prepare(heartbeat, timeout=1)
    now[0] = 1
    assert heartbeat.accept(reply(), generation=1) is None
    with pytest.raises(ValueError, match="pending"):
        prepare(heartbeat)
    assert heartbeat.poll().status == "timeout"
    assert heartbeat.poll() is None
    assert source.allocate() == 18
    second = prepare(heartbeat)
    assert heartbeat.cancel(request.ticket) is None
    assert heartbeat.close().ticket == second.ticket
    assert heartbeat.close() is None


def test_retired_namespace_does_not_deliver_old_reply():
    heartbeat, source = owner()
    request = prepare(heartbeat)
    source.close()
    assert heartbeat.accept(reply(), generation=1) is None
    with pytest.raises(ValueError, match="closed"):
        prepare(heartbeat)
    assert heartbeat.close().ticket == request.ticket


@pytest.mark.parametrize("timeout", [0, -1, 11, True, float("nan"), float("inf")])
def test_invalid_deadline_does_not_allocate(timeout):
    heartbeat, source = owner()
    with pytest.raises(ValueError):
        prepare(heartbeat, timeout=timeout)
    assert source.allocate() == 17


def test_wrong_node_and_generation_cannot_complete():
    heartbeat, source = owner()
    with pytest.raises(ValueError, match="identity"):
        heartbeat.prepare(replace(NODE, session_id=99), "192.0.2.1", 1234)
    prepare(heartbeat)
    assert heartbeat.accept(reply(), generation=2) is None
    assert heartbeat.cancel(ReceiptTicket(2, 17, 0xA0)) is None
    assert heartbeat.close().status == "cancelled"
    assert source.allocate() == 18


def test_builder_failure_burns_sequence_without_leaving_waiter(monkeypatch):
    from backend.app.drivers.yoosee.p2p import broker_heartbeat as module

    heartbeat, source = owner()
    def fail(*args): raise ValueError("builder rejected endpoint")
    monkeypatch.setattr(module, "build_heartbeat", fail)
    with pytest.raises(ValueError):
        prepare(heartbeat)
    assert source.allocate() == 18 and heartbeat.close() is None


def test_shared_encrypted_fragmented_dispatch_does_not_steal_read_reply():
    from backend.app.drivers.yoosee.p2p.broker_maintenance import BrokerMaintenance
    from backend.app.drivers.yoosee.p2p.broker_reads import BrokerReads
    from backend.app.drivers.yoosee.p2p.wire import finish_mode2
    from tests.test_broker_reads import DEVICE, PATH, response
    from tests.test_vendor_resource_service_protocol import _fragment

    heartbeat, source = owner()
    reads = BrokerReads(NODE.session_id, 1, 17, sequences=source)
    request = reads.register(DEVICE.device_id, PATH, 1)
    beat = prepare(heartbeat)
    maintenance = BrokerMaintenance(NODE)
    completed = []
    def inspect(plain):
        for value in (reads.accept(plain, generation=1), heartbeat.accept(plain, generation=1)):
            if value is not None: completed.append(value)
    wire = finish_mode2(bytearray(reply(sequence=beat.ticket.sequence)), NODE.session_key)
    for index in (1, 0):
        maintenance.receive(_fragment(index, 2, wire, 16), NODE.address, inspect_message=inspect)
    assert len(completed) == 1 and completed[0].ticket == beat.ticket
    assert reads.pending_count == 1
    maintenance.receive(finish_mode2(bytearray(response(request)), NODE.session_key),
                        NODE.address, inspect_message=inspect)
    assert len(completed) == 2 and completed[1].request == request
    assert reads.pending_count == 0
    maintenance.close()
    reads.close()
    heartbeat.close()
