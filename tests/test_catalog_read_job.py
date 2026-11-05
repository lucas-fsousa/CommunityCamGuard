import json
import struct

import pytest

from backend.app.drivers.yoosee.p2p.broker_sequences import BrokerSequences
from backend.app.drivers.yoosee.p2p.catalog_read_inbox import CatalogReadInbox
from backend.app.drivers.yoosee.p2p.catalog_read_job import (
    CatalogReadJob,
    CatalogReadResult,
    CatalogReadSpec,
)
from backend.app.drivers.yoosee.p2p.crypto import gute_mode2_decrypt
from tests.test_broker_reads import NODE
from tests.test_broker_transactions import ack
from tests.test_vendor_alarm_voice_catalog import _payload
from tests.test_vendor_resource_service_session import _plain


def response(sequence=40, *, system=True, payload=None):
    frame = bytearray(_plain(0xC1, _payload(system=system, number=7) if payload is None else payload,
                             flags=2 << 16))
    struct.pack_into('<Q', frame, 4, NODE.session_id)
    struct.pack_into('<I', frame, 16, sequence)
    return bytes(frame)


def make(system=True):
    clock = [0.0]
    sequences = BrokerSequences(NODE.session_id, 1, 40)
    job = CatalogReadJob(NODE, sequences, CatalogReadSpec(123, system), generation=1,
                         deadline=2, clock=lambda: clock[0])
    return job, sequences, clock


@pytest.mark.parametrize('system', [False, True])
def test_fixed_query_sent_once_and_sanitized_complete_reply(system):
    job, sequences, _ = make(system)
    plain = gute_mode2_decrypt(job.next_packet(), NODE.session_key)
    length = struct.unpack_from('<H', plain, 30)[0]
    cursor = 32+plain[29]+1
    query = json.loads(plain[cursor:cursor+length])
    assert query['http'] == {'url':'/resfile/queryres', 'type':'POST'}
    assert query['data']['bySys'] == int(system) and query['data']['resTypes'] == [4]
    assert job.next_packet() is None
    job.observe(ack(40, 0xC0, NODE.session_id), NODE.address, generation=1)
    assert job.result is None  # Transport ACK is not a catalogue.
    job.observe(response(system=system), NODE.address, generation=1)
    assert job.result.status == 'response' and job.result.transport_acknowledged
    assert job.result.catalog.resources[0].key == ('system-7' if system else 'custom-7')
    assert 'secret.invalid' not in repr(job.result)
    assert sequences.allocate() == 41 and not sequences.closed


@pytest.mark.parametrize('mismatch', ['peer', 'generation', 'session', 'sequence', 'mode', 'ack', 'kind', 'unsent', 'late'])
def test_wrong_reply_cannot_complete_or_expand(monkeypatch, mismatch):
    job, _, clock = make()
    if mismatch != 'unsent':
        job.next_packet()
    wire = bytearray(response())
    peer, generation = NODE.address, 1
    if mismatch == 'peer': peer = ('192.0.2.99', 9)
    elif mismatch == 'generation': generation = 2
    elif mismatch == 'session': struct.pack_into('<Q', wire, 4, NODE.session_id+1)
    elif mismatch == 'sequence': struct.pack_into('<I', wire, 16, 39)
    elif mismatch == 'mode': struct.pack_into('<I', wire, 20, 1 << 16)
    elif mismatch == 'ack': struct.pack_into('<I', wire, 20, (2 << 16) | (1 << 20))
    elif mismatch == 'kind': wire[1] = 0xD3
    elif mismatch == 'late': clock[0] = 2
    monkeypatch.setattr('backend.app.drivers.yoosee.p2p.catalog_read_job._expand_compressed_frame',
                        lambda *_: pytest.fail('unmatched reply expanded'))
    job.observe(bytes(wire), peer, generation=generation)
    assert job.result is None


@pytest.mark.parametrize('bad', [b'{}', b'not json', _payload(system=False, number=7),
    b'{"code":0,"data":{"total":2,"urls":[]}}'])
def test_invalid_or_partial_catalogue_fails_without_replay(bad):
    job, sequences, _ = make()
    job.next_packet()
    job.observe(response(payload=bad), NODE.address, generation=1)
    assert job.result.status == 'invalid_response'
    assert job.next_packet() is None and not sequences.closed


def test_timeout_rejection_and_cancellation_release_only_private_waiter():
    for status in ('timeout', 'transport_rejected', 'cancelled'):
        job, sequences, clock = make()
        job.next_packet()
        if status == 'timeout': clock[0] = 2; job.next_packet()
        elif status == 'cancelled': job.cancel()
        else: job.observe(ack(40, 0xC0, NODE.session_id, result=1), NODE.address, generation=1)
        assert job.result.status == status and job.next_packet() is None
        assert sequences.allocate() == 41


@pytest.mark.parametrize('access,system', [(True, True), (0, True), (-1, True), (1 << 64, True), (123, 1), (123, 'true')])
def test_only_typed_catalogue_scope_admitted(access, system):
    with pytest.raises(ValueError): CatalogReadSpec(access, system)


def test_one_slot_cancellation_retirement_and_unread_completed_cancel():
    inbox = CatalogReadInbox(1, clock=lambda: 0)
    ticket = inbox.submit(CatalogReadSpec(123, True))
    assert inbox.claim() is ticket
    inbox.cancel(ticket)
    with pytest.raises(ValueError): inbox.submit(CatalogReadSpec(123, False))
    inbox.finish(ticket, CatalogReadResult('cancelled'))
    other = inbox.submit(CatalogReadSpec(123, False))
    inbox.claim(); inbox.finish(other, CatalogReadResult('response'))
    inbox.cancel(other)
    final = inbox.submit(CatalogReadSpec(123, True))
    inbox.close()
    assert inbox.take(final).status == 'cancelled'
    with pytest.raises(ValueError): inbox.submit(CatalogReadSpec(123, True))
