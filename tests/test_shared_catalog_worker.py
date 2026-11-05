"""Encrypted sole-reader catalogue, fragmentation, media route/B7/heartbeat coexistence."""

import struct

import pytest

from backend.app.drivers.yoosee.p2p.account_read_worker import AccountReadWorker
from backend.app.drivers.yoosee.p2p.catalog_read_job import CatalogReadSpec
from backend.app.drivers.yoosee.p2p.crypto import gute_mode2_decrypt, gute_mode2_encrypt
from backend.app.drivers.yoosee.p2p.wire import finish_mode2
from tests.test_account_media_route import SPEC, MediaSocket
from tests.test_broker_reads import DEVICE, NODE, PATH
from tests.test_broker_transactions import ack
from tests.test_catalog_read_job import response
from tests.test_vendor_resource_service_protocol import _fragment


class Socket(MediaSocket):
    def __init__(self, fragmented=False, compressed=False):
        super().__init__()
        self.catalogs = self.catalog_acks = self.fragments_acks = 0
        self.fragmented = fragmented
        self.compressed = compressed

    def sendto(self, wire, peer):
        if wire[:2] == b'\x70\x02':
            self.fragments_acks += 1
            return len(wire)
        plain = gute_mode2_decrypt(wire, NODE.session_key)
        flags = struct.unpack_from('<I', plain, 20)[0]
        if flags & (1 << 20) and plain[1] == 0xC1:
            self.catalog_acks += 1
        if not flags & (1 << 20) and plain[1] == 0xC0:
            self.catalogs += 1
            sequence = struct.unpack_from('<I', plain, 12)[0]
            self.sequences.append(sequence)
            self.queue.append(finish_mode2(bytearray(ack(sequence, 0xC0, NODE.session_id)), NODE.session_key))
            reply = bytearray(response(sequence))
            struct.pack_into('<I', reply, 20, (2 << 16) | (3 << 18))
            if self.compressed:
                body = bytes(reply[24:])
                # A valid level2 literal block exercises the bounded decoder.
                block = bytes([10])+struct.pack('<II', len(body)+9, len(body))+body
                reply = reply[:24]+block
                struct.pack_into('<H', reply, 2, len(reply))
                struct.pack_into('<I', reply, 20, (2 << 16) | (3 << 18) | 1 | (len(body) << 1))
            # C1 offset16 echoes the request; finish_mode2 would overwrite it
            # with an outgoing-request checksum, producing a stale response.
            encrypted = gute_mode2_encrypt(bytes(reply), NODE.session_key)
            if self.fragmented:
                count = (len(encrypted)+127)//128
                self.queue.extend(_fragment(i, count, encrypted, 128) for i in reversed(range(count)))
            else:
                self.queue.append(encrypted)
            return len(wire)
        return super().sendto(wire, peer)


@pytest.mark.parametrize('fragmented', [False, True])
@pytest.mark.parametrize('compressed', [False, True])
def test_catalogue_reads_never_create_a_second_reader_and_ack_only_once(fragmented, compressed):
    sock = Socket(fragmented, compressed)
    owner = AccountReadWorker(NODE, 1, '192.0.2.1', 1234, state_reads=True, clock=lambda: sock.now)
    read = owner.mailbox.submit(DEVICE.device_id, PATH)
    catalog = owner.catalog_inbox.submit(CatalogReadSpec(123, True))
    result = owner.run(sock, initial_media=SPEC, cancelled=lambda: sock.now >= 55, wait=sock.wait)
    assert result.reason == 'stopped' and result.media.broker_release
    assert sock.opens == sock.closes == sock.reads == sock.beats == sock.closed == 1
    assert sock.catalogs == sock.catalog_acks == 1
    assert (sock.fragments_acks > 1) is fragmented
    assert len(sock.sequences) == len(set(sock.sequences)) == 5
    assert owner.mailbox.take(read).status == 'response'
    state = owner.catalog_inbox.take(catalog)
    assert state.status == 'response' and state.transport_acknowledged
    assert state.catalog.resources[0].key == 'system-7'


def test_identity_only_worker_has_no_catalogue_admission():
    owner = AccountReadWorker(NODE, 1, '192.0.2.1', 1234)
    assert owner.catalog_inbox is None
