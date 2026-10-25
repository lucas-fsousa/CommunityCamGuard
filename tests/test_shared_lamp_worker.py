"""Synthetic encrypted single socket: media route, lamp read, B7 and heartbeat together."""

import json
import struct
from types import SimpleNamespace

from backend.app.drivers.yoosee.p2p.account_read_worker import AccountReadWorker
from backend.app.drivers.yoosee.p2p.crypto import gute_mode2_decrypt, gute_mode2_encrypt
from backend.app.drivers.yoosee.p2p.lamp_read_job import LampReadSpec
from backend.app.drivers.yoosee.p2p.wire import finish_mode2
from tests.test_account_media_route import SPEC, MediaSocket
from tests.test_broker_reads import DEVICE, NODE, PATH
from tests.test_broker_transactions import ack
from tests.test_lamp_read_job import response


class Socket(MediaSocket):
    def __init__(self):
        super().__init__()
        self.lamp_reads = self.lamp_receipts = 0
        self.lamp_message_acks = 0

    def sendto(self, wire, peer):
        plain = gute_mode2_decrypt(wire, NODE.session_key)
        flags = struct.unpack_from("<I", plain, 20)[0]
        if flags & (1 << 20) and plain[1] == 0xB9 and struct.unpack_from("<I", plain, 12)[0] == 200:
            self.lamp_message_acks += 1
        if not flags & (1 << 20):
            sequence = struct.unpack_from("<I", plain, 12)[0]
            if plain[1] == 0xB9 and plain[52:56] == b"\x01\xff\0\0":
                assert json.loads(plain[60:]) == {"type":12}  # no type11 or set value
                self.sequences.append(sequence)
                self.lamp_reads += 1
                self.queue.append(finish_mode2(bytearray(ack(sequence, session=NODE.session_id)), NODE.session_key))
                result = response(SimpleNamespace(_request_id=sequence), node=NODE, session=NODE.session_id)
                self.queue.append(gute_mode2_encrypt(result, NODE.session_key))
                return len(wire)
            if plain[1] == 0xBA:
                self.sequences.append(sequence)
                self.lamp_receipts += 1
                return len(wire)
        return super().sendto(wire, peer)


def test_one_reader_media_and_state_reads_keep_unique_sequences_and_close_cleanly():
    sock = Socket()
    owner = AccountReadWorker(NODE, 1, "192.0.2.1", 1234, state_reads=True, clock=lambda: sock.now)
    read = owner.mailbox.submit(DEVICE.device_id, PATH)
    lamp = owner.lamp_inbox.submit(LampReadSpec(123, DEVICE.device_id))
    result = owner.run(sock, initial_media=SPEC, cancelled=lambda: sock.now >= 55, wait=sock.wait)
    assert result.reason == "stopped" and result.media.broker_release
    assert sock.opens == sock.closes == sock.reads == sock.beats == sock.closed == 1
    assert sock.lamp_reads == sock.lamp_receipts == 1
    assert sock.lamp_message_acks == 1  # never ACK the same reply through two owners
    assert len(sock.sequences) == len(set(sock.sequences)) == 6
    assert owner.mailbox.take(read).status == "response"
    state = owner.lamp_inbox.take(lamp)
    assert state.status == "response" and state.enabled is False and state.transport_acknowledged
