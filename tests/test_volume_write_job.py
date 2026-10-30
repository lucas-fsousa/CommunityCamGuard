import struct

import pytest

from backend.app.drivers.yoosee.p2p.account_read_worker import AccountReadWorker
from backend.app.drivers.yoosee.p2p.broker_sequences import BrokerSequences
from backend.app.drivers.yoosee.p2p.crypto import gute_mode2_decrypt
from backend.app.drivers.yoosee.p2p.model_control_inbox import ControlWriteInbox
from backend.app.drivers.yoosee.p2p.model_control_job import (
    ControlWriteJob,
    ControlWriteResult,
    NightVisionWriteSpec,
    OrientationWriteSpec,
    VolumeWriteSpec,
)
from backend.app.drivers.yoosee.p2p.wire import finish_mode2, new_header
from tests.test_account_read_worker import WorkerSocket
from tests.test_broker_reads import NODE as WORKER_NODE
from tests.test_broker_transactions import ack
from tests.test_lamp_read_job import NODE


def fixture(spec=None):
    now = [0.0]
    seq = BrokerSequences(NODE.session_id, 1, 40)
    job = ControlWriteJob(NODE, seq, spec or VolumeWriteSpec(7000000002, 50),
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
    assert job.result == ControlWriteResult('response', 0, True)
    assert seq.matches(17, 1) and seq.allocate() == 41
    assert job.next_packet() is None


@pytest.mark.parametrize('fault', ['peer', 'generation', 'session', 'message', 'unsent', 'expired'])
@pytest.mark.parametrize('spec', [VolumeWriteSpec(7000000002, 50), OrientationWriteSpec(7000000002, 'inverted'), NightVisionWriteSpec(7000000002, 'night')])
def test_unrelated_or_late_reply_does_not_confirm_write(fault, spec):
    job, _, now = fixture(spec)
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
    inbox = ControlWriteInbox(1)
    ticket = inbox.submit(VolumeWriteSpec(123, 50));assert inbox.claim() is ticket
    inbox.cancel(ticket)
    with pytest.raises(ValueError):inbox.submit(VolumeWriteSpec(123, 75))
    inbox.finish(ticket, ControlWriteResult('cancelled'))
    next_ticket = inbox.submit(VolumeWriteSpec(123, 75))
    assert inbox.take(ticket) is None
    inbox.close();assert inbox.take(next_ticket).status == 'cancelled'


@pytest.mark.parametrize('percent', [True, 1, 30, -1, '50'])
def test_only_five_integer_volume_positions_are_accepted(percent):
    with pytest.raises(ValueError):VolumeWriteSpec(123, percent)


@pytest.mark.parametrize('spec', [VolumeWriteSpec(7000000002, 50), OrientationWriteSpec(7000000002, 'inverted'), NightVisionWriteSpec(7000000002, 'night')])
def test_existing_worker_sends_typed_control_on_its_only_socket_and_shared_sequences(monkeypatch, spec):
    sock = WorkerSocket()
    owner = AccountReadWorker(WORKER_NODE, 1, '192.0.2.1', 1234,
                              state_reads=True, clock=lambda: sock.now)
    ticket = owner.control_inbox.submit(spec)
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
    assert owner.control_inbox.take(ticket) == ControlWriteResult('response', 0, True)


@pytest.mark.parametrize('orientation,raw', [('normal', 1), ('inverted', 3)])
def test_shared_job_emits_only_the_typed_orientation_property(orientation, raw):
    seq = BrokerSequences(NODE.session_id, 1, 40)
    job = ControlWriteJob(NODE, seq, OrientationWriteSpec(7000000002, orientation),
                          generation=1, deadline=2, clock=lambda: 0)
    plain = gute_mode2_decrypt(job.next_packet(), NODE.session_key)
    assert plain[1] == 0xD2 and b'ProWritable.videoParm.setVal.multiFlip\0' in plain
    assert plain.endswith(str(raw).encode()+b'\0')
    assert job.next_packet() is None
    job.observe(response(job), NODE.address, generation=1)
    assert job.result.status == 'response' and job.result.error_code == 0


@pytest.mark.parametrize('value', [True, 3, 'mirror', 'flip', '', None])
def test_orientation_spec_rejects_unreviewed_states(value):
    with pytest.raises(ValueError):OrientationWriteSpec(123, value)


def test_volume_and_orientation_share_one_inbox_slot():
    inbox = ControlWriteInbox(1)
    ticket = inbox.submit(VolumeWriteSpec(123, 50))
    with pytest.raises(ValueError):inbox.submit(OrientationWriteSpec(123, 'inverted'))
    inbox.cancel(ticket)
    assert inbox.submit(OrientationWriteSpec(123, 'inverted')).spec.orientation == 'inverted'


@pytest.mark.parametrize('mode,raw', [('automatic', 0), ('daytime', 1), ('night', 2)])
def test_shared_job_only_writes_night_mode_not_adjacent_video_fields(mode, raw):
    job, seq, _ = fixture(NightVisionWriteSpec(7000000002, mode))
    plain = gute_mode2_decrypt(job.next_packet(), NODE.session_key)
    assert plain[1] == 0xD2
    assert b'ProWritable.videoParm.setVal.nightViewMode\0' in plain
    assert b'multiFlip' not in plain and b'nightViewModeV2' not in plain
    assert plain.endswith(str(raw).encode() + b'\0')
    assert job.next_packet() is None
    job.observe(response(job), NODE.address, generation=1)
    assert job.result == ControlWriteResult('response', 0, False)
    assert seq.matches(17, 1)


@pytest.mark.parametrize('mode', [True, 0, 2, 'infrared', 'auto', '', None])
def test_night_mode_rejects_unreviewed_states(mode):
    with pytest.raises(ValueError):
        NightVisionWriteSpec(123, mode)


def test_night_mode_uses_the_same_single_slot_as_other_controls():
    inbox = ControlWriteInbox(1)
    first = inbox.submit(NightVisionWriteSpec(123, 'night'))
    assert inbox.claim() is first
    with pytest.raises(ValueError):
        inbox.submit(OrientationWriteSpec(123, 'normal'))
    inbox.cancel(first)
    with pytest.raises(ValueError):
        inbox.submit(VolumeWriteSpec(123, 50))
    inbox.finish(first, ControlWriteResult('cancelled'))
    assert inbox.submit(VolumeWriteSpec(123, 50)).spec.percent == 50
