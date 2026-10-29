import struct

import pytest

from backend.app.drivers.yoosee.p2p.account_read_worker import AccountReadWorker
from backend.app.drivers.yoosee.p2p.broker_sequences import BrokerSequences
from backend.app.drivers.yoosee.p2p.crypto import gute_mode2_decrypt
from backend.app.drivers.yoosee.p2p.volume_write_inbox import VolumeWriteInbox
from backend.app.drivers.yoosee.p2p.volume_write_job import (
    VolumeWriteJob,
    VolumeWriteResult,
    VolumeWriteSpec,
)
from backend.app.drivers.yoosee.p2p.wire import finish_mode2, new_header
from tests.test_account_read_worker import WorkerSocket
from tests.test_broker_reads import NODE as WORKER_NODE
from tests.test_broker_transactions import ack
from tests.test_lamp_read_job import NODE


def fixture():
    now = [0.0]
    seq = BrokerSequences(NODE.session_id, 1, 40)
    job = VolumeWriteJob(NODE, seq, VolumeWriteSpec(7000000002, 50),
                         generation=1, deadline=2, clock=lambda: now[0])
    return job, seq, now


def response(job, *, session=17, message=None, error=0):
    wire = new_header(0xD3, 54, session, 200, (2 << 16) | (1 << 18))
    struct.pack_into('<I', wire, 48, job._message_id if message is None else message)
    struct.pack_into('<H', wire, 52, error)
    return gute_mode2_decrypt(finish_mode2(wire, NODE.session_key), NODE.session_key)


def test_typed_write_sends_once_and_transport_ack_is_not_success():
    job, seq, _ = fixture()
    wire = gute_mode2_decrypt(job.next_packet(), NODE.session_key)
    assert wire[1] == 0xD2 and b'ProWritable.volume.setVal' in wire and wire.endswith(b'5\0')
    assert job.next_packet() is None
    job.observe(ack(40, kind=0xD2, session=17), NODE.address, generation=1)
    assert job.result is None
    job.observe(response(job), NODE.address, generation=1)
    assert job.result == VolumeWriteResult('response', 0, True)
    assert seq.matches(17, 1) and seq.allocate() == 41
    assert job.next_packet() is None


@pytest.mark.parametrize('fault', ['peer', 'generation', 'session', 'message', 'unsent', 'expired'])
def test_unrelated_or_late_reply_does_not_confirm_write(fault):
    job, _, now = fixture()
    if fault != 'unsent':job.next_packet()
    if fault == 'expired':now[0] = 2
    wire = response(job, session=18 if fault == 'session' else 17,
                    message=job._message_id ^ 1 if fault == 'message' else None)
    job.observe(wire, ('192.0.2.99', 1) if fault == 'peer' else NODE.address,
                generation=2 if fault == 'generation' else 1)
    assert job.result is None


def test_timeout_cancel_and_rejection_never_replay_or_close_shared_namespace():
    job, seq, now = fixture();job.next_packet();now[0] = 2
    assert job.next_packet() is None and job.result.status == 'timeout'
    assert seq.matches(17, 1)
    other, seq, _ = fixture();other.next_packet();other.cancel()
    assert other.result.status == 'cancelled' and other.next_packet() is None and seq.matches(17, 1)
    rejected, _, _ = fixture();rejected.next_packet()
    rejected.observe(ack(40, kind=0xD2, session=17, result=7), NODE.address, generation=1)
    assert rejected.result.status == 'transport_rejected'


def test_inbox_keeps_claimed_cancelled_write_until_owner_retires_it():
    inbox = VolumeWriteInbox(1)
    ticket = inbox.submit(VolumeWriteSpec(123, 50));assert inbox.claim() is ticket
    inbox.cancel(ticket)
    with pytest.raises(ValueError):inbox.submit(VolumeWriteSpec(123, 75))
    inbox.finish(ticket, VolumeWriteResult('cancelled'))
    next_ticket = inbox.submit(VolumeWriteSpec(123, 75))
    assert inbox.take(ticket) is None
    inbox.close();assert inbox.take(next_ticket).status == 'cancelled'


@pytest.mark.parametrize('percent', [True, 1, 30, -1, '50'])
def test_only_five_integer_volume_positions_are_accepted(percent):
    with pytest.raises(ValueError):VolumeWriteSpec(123, percent)


def test_existing_worker_sends_volume_on_its_only_socket_and_shared_sequences(monkeypatch):
    sock = WorkerSocket()
    owner = AccountReadWorker(WORKER_NODE, 1, '192.0.2.1', 1234,
                              state_reads=True, clock=lambda: sock.now)
    ticket = owner.volume_inbox.submit(VolumeWriteSpec(7000000002, 50))
    original = sock.sendto
    writes = []
    def send(wire, peer):
        plain = gute_mode2_decrypt(wire, WORKER_NODE.session_key)
        if plain[1] == 0xD2 and not struct.unpack_from('<I', plain, 20)[0] & (1 << 20):
            writes.append(wire)
            sequence = struct.unpack_from('<I', plain, 12)[0]
            receipt = ack(sequence, kind=0xD2, session=WORKER_NODE.session_id)
            sock.queue.append(finish_mode2(bytearray(receipt), WORKER_NODE.session_key))
            reply = new_header(0xD3, 54, WORKER_NODE.session_id, 200, (2 << 16) | (1 << 18))
            struct.pack_into('<I', reply, 48, struct.unpack_from('<I', plain, 32)[0])
            sock.queue.append(finish_mode2(reply, WORKER_NODE.session_key))
            return len(wire)
        return original(wire, peer)
    monkeypatch.setattr(sock, 'sendto', send)
    result = owner.run(sock, cancelled=lambda: ticket.completion.done() or sock.now > 4)
    assert result.reason == 'stopped' and sock.closed == 1 and len(writes) == 1
    assert owner.volume_inbox.take(ticket) == VolumeWriteResult('response', 0, True)
