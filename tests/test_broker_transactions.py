import struct

import pytest

from backend.app.drivers.yoosee.p2p.broker_maintenance import BrokerMaintenance
from backend.app.drivers.yoosee.p2p.broker_transactions import (
    MAX_PENDING,
    BrokerTransactions,
    ReceiptTicket,
)
from backend.app.drivers.yoosee.p2p.contracts import CertifiedNode
from backend.app.drivers.yoosee.p2p.crypto import gute_mode1_xor_checksum
from backend.app.drivers.yoosee.p2p.wire import finish_mode2, new_header
from tests.test_vendor_resource_service_protocol import _fragment


def ack(sequence=40, kind=0xB9, session=17, result=0, flags=(2 << 16) | (1 << 20)):
    frame = new_header(kind, 32, session, sequence, flags)
    struct.pack_into("<H", frame, 26, result)
    struct.pack_into("<I", frame, 16, gute_mode1_xor_checksum(frame))
    return bytes(frame)


def test_interleaved_receipts_never_steal_another_transaction():
    owner = BrokerTransactions(17, 1, 40)
    tickets = [owner.register(kind, 1) for kind in (0xB9, 0xB7, 0xB9)]
    for i in (2, 0, 1):
        ticket = tickets[i]
        result = owner.accept(ack(ticket.sequence, ticket.kind), generation=1)
        assert result.ticket == ticket and result.accepted
        assert owner.accept(ack(ticket.sequence, ticket.kind), generation=1) is None
    assert owner.pending_count == 0


@pytest.mark.parametrize("wire,generation", [
    (ack(kind=0xB7), 1), (ack(sequence=41), 1), (ack(session=18), 1),
    (ack(), 2), (ack(), True), (ack(flags=2 << 16), 1),
    (ack(flags=(1 << 16) | (1 << 20)), 1), (ack()[:-1], 1),
    (ack() + b"x", 1), (bytes(32), 1),
])
def test_mismatches_leave_waiter_pending(wire, generation):
    owner = BrokerTransactions(17, 1, 40)
    owner.register(0xB9, 1)
    assert owner.accept(wire, generation=generation) is None
    assert owner.pending_count == 1


@pytest.mark.parametrize("offset", [0, 2, 4, 12, 16, 20, 26])
def test_corrupt_header_never_completes(offset):
    owner = BrokerTransactions(17, 1, 40)
    owner.register(0xB9, 1)
    wire = bytearray(ack())
    wire[offset] ^= 1
    assert owner.accept(bytes(wire), generation=1) is None


@pytest.mark.parametrize("result", [1, 4, 65535])
def test_negative_receipt_is_terminal_but_not_positive_or_retried(result):
    owner = BrokerTransactions(17, 1, 40)
    owner.register(0xB9, 1)
    receipt = owner.accept(ack(result=result), generation=1)
    assert receipt.result == result and not receipt.accepted
    assert owner.pending_count == 0


def test_expiry_is_explicit_and_late_packets_cannot_match_reused_sequence():
    now = [0.0]
    owner = BrokerTransactions(17, 1, 40, clock=lambda: now[0])
    ticket = owner.register(0xB9, 1)
    now[0] = 1
    assert owner.accept(ack(), generation=1) is None
    assert owner.pending_count == 1
    assert owner.poll() == (ticket,) and owner.poll() == ()
    assert owner.register(0xB9, 1).sequence == 41
    assert owner.accept(ack(), generation=1) is None


def test_capacity_and_sequence_wrap_fail_closed_without_eviction():
    owner = BrokerTransactions(17, 1, 0xFFFFFFFF)
    ticket = owner.register(0xB9, 1)
    assert owner.accept(ack(sequence=ticket.sequence), generation=1).accepted
    with pytest.raises(ValueError, match="sequence"):
        owner.register(0xB9, 1)
    owner = BrokerTransactions(17, 1, 0)
    for _ in range(MAX_PENDING):
        owner.register(0xB9, 1)
    with pytest.raises(ValueError, match="capacity"):
        owner.register(0xB9, 1)
    assert owner.pending_count == MAX_PENDING
    assert len(owner.close()) == MAX_PENDING
    assert owner.close() == () and owner.pending_count == 0
    assert owner.accept(ack(sequence=0), generation=1) is None
    with pytest.raises(ValueError, match="closed"):
        owner.register(0xB9, 1)


@pytest.mark.parametrize("timeout", [0, -1, 11, float("nan"), float("inf"), True, "1"])
def test_unbounded_timeout_rejected(timeout):
    owner = BrokerTransactions(17, 1, 40)
    with pytest.raises(ValueError):
        owner.register(0xB9, timeout)
    assert owner.pending_count == 0


def test_cancel_does_not_recycle_sequence_or_cancel_other_generation():
    owner = BrokerTransactions(17, 2, 40)
    ticket = owner.register(0xB9, 1)
    assert not owner.cancel(ReceiptTicket(1, 40, 0xB9))
    assert owner.cancel(ticket) and not owner.cancel(ticket)
    assert owner.register(0xB9, 1).sequence == 41
    assert owner.accept(ack(), generation=2) is None


def test_old_generation_callback_cannot_complete_new_owner_with_same_sequence():
    old = BrokerTransactions(17, 1, 40)
    ticket = old.register(0xB9, 1)
    assert old.close() == (ticket,)
    current = BrokerTransactions(17, 2, 40)
    current.register(0xB9, 1)
    assert current.accept(ack(), generation=1) is None
    assert current.pending_count == 1
    assert current.accept(ack(), generation=2).accepted


@pytest.mark.parametrize("fault", [None, "peer", "session", "generation"])
def test_encrypted_out_of_order_fragments_only_deliver_complete_current_receipt(fault):
    node = CertifiedNode(("192.0.2.10", 19800), 17, bytes(range(32)), 40)
    maintenance = BrokerMaintenance(node)
    owner = BrokerTransactions(17, 1, 40)
    owner.register(0xB9, 1)
    wire = finish_mode2(bytearray(ack(session=18 if fault == "session" else 17)), node.session_key)
    completed = []
    def inspect(plain):
        receipt = owner.accept(plain, generation=2 if fault == "generation" else 1)
        if receipt is not None:
            completed.append(receipt)
    peer = (node.address[0], 1) if fault == "peer" else node.address
    maintenance.receive(_fragment(1, 2, wire, 16), peer, inspect_message=inspect)
    assert not completed and owner.pending_count == 1
    maintenance.receive(_fragment(0, 2, wire, 16), peer, inspect_message=inspect)
    assert len(completed) == (0 if fault else 1)
    assert owner.pending_count == (1 if fault else 0)
    maintenance.close()
    owner.close()


def test_unknown_and_duplicate_traffic_do_not_extend_deadlines():
    now = [0.0]
    owner = BrokerTransactions(17, 1, 40, clock=lambda: now[0])
    ticket = owner.register(0xB9, 1)
    for index in range(1000):
        now[0] = index / 1000
        assert owner.accept(ack(sequence=41), generation=1) is None
    now[0] = 1
    assert owner.poll() == (ticket,)
