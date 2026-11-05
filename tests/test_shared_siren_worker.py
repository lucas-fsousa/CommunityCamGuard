"""Synthetic encrypted siren/media owner and shutdown drain; no camera/network I/O."""

import struct

import pytest

from backend.app.drivers.yoosee.p2p.account_read_worker import AccountReadWorker
from backend.app.drivers.yoosee.p2p.crypto import gute_mode2_decrypt, gute_mode2_encrypt
from backend.app.drivers.yoosee.p2p.ptz_scheduler import PtzSpec
from backend.app.drivers.yoosee.p2p.siren_pulse_job import SirenPulseSpec
from backend.app.drivers.yoosee.p2p.wire import finish_mode2, new_header
from tests.test_account_media_route import SPEC, MediaSocket
from tests.test_broker_reads import DEVICE, NODE, PATH
from tests.test_broker_transactions import ack
from tests.test_vendor_p2p_model_session import _response


class Socket(MediaSocket):
    def __init__(self, fault=None):
        super().__init__()
        self.fault = fault
        self.ons = self.offs = self.states = self.action_receipts = 0

    def sendto(self, wire, peer):
        plain = gute_mode2_decrypt(wire, NODE.session_key)
        flags = struct.unpack_from('<I', plain, 20)[0]
        if flags & (1 << 20):
            if plain[1] == 0xAD: self.action_receipts += 1
        elif plain[1] == 0xAC:
            on = plain.endswith(b'2\0')
            if on: self.ons += 1
            else: self.offs += 1
            sequence = struct.unpack_from('<I', plain, 12)[0]
            self.sequences.append(sequence)
            if self.fault == ('on_send' if on else 'off_send'): raise OSError('ambiguous send')
            self.queue.append(finish_mode2(bytearray(ack(sequence, 0xAC, NODE.session_id)), NODE.session_key))
            if on or self.fault != 'off_silent':
                reply = new_header(0xAD, 54, NODE.session_id, 200+int(on), (2 << 16) | (1 << 18))
                struct.pack_into('<I', reply, 48, struct.unpack_from('<I', plain, 32)[0])
                self.queue.append(finish_mode2(reply, NODE.session_key))
            return len(wire)
        elif plain[1] == 0xB7 and b'Action.expelCtrl' in plain:
            self.states += 1
            sequence = struct.unpack_from('<I', plain, 12)[0]
            self.sequences.append(sequence)
            reply = _response(NODE, DEVICE, {'t':1, 'stVal':2 if self.fault == 'still_on' else 1}, sequence=sequence)
            self.queue.append(gute_mode2_encrypt(reply, NODE.session_key))
            return len(wire)
        return super().sendto(wire, peer)


def worker(sock):
    return AccountReadWorker(NODE, 1, '192.0.2.1', 1234, state_reads=True, clock=lambda: sock.now)


def test_siren_media_read_and_heartbeat_share_one_socket_and_unique_sequences():
    sock = Socket()
    owner = worker(sock)
    read = owner.mailbox.submit(DEVICE.device_id, PATH)
    pulse = owner.siren_inbox.submit(SirenPulseSpec(123, DEVICE.device_id, 2))
    result = owner.run(sock, initial_media=SPEC, cancelled=lambda: sock.now >= 55, wait=sock.wait)
    assert result.reason == 'stopped' and result.media.broker_release
    assert sock.ons == sock.offs == sock.states == sock.opens == sock.closes == sock.beats == sock.closed == 1
    assert sock.action_receipts == 2  # No duplicate generic AD ACK.
    assert len(sock.sequences) == len(set(sock.sequences)) == 7
    assert owner.mailbox.take(read).status == 'response'
    assert owner.siren_inbox.take(pulse).status == 'completed'
    assert result.siren.off_confirmed and result.siren.on_error == result.siren.off_error == 0


@pytest.mark.parametrize('fault', ['off_silent', 'still_on', 'on_send', 'off_send'])
def test_failures_never_repeat_on_and_unknown_off_poison_the_owner(fault):
    sock = Socket(fault)
    owner = worker(sock)
    owner.siren_inbox.submit(SirenPulseSpec(123, DEVICE.device_id, 2))
    result = owner.run(sock, initial_media=SPEC, cancelled=lambda: sock.now >= 8, wait=sock.wait)
    assert sock.ons == 1 and 1 <= sock.offs <= 2 and sock.closed == 1
    assert result.siren.start_attempted
    assert result.siren.off_confirmed is (fault == 'on_send')
    assert sock.now < 9
    with pytest.raises(ValueError): owner.siren_inbox.submit(SirenPulseSpec(123, DEVICE.device_id, 2))


def test_shutdown_after_on_drains_off_and_state_on_the_original_socket():
    sock = Socket()
    owner = worker(sock)
    owner.siren_inbox.submit(SirenPulseSpec(123, DEVICE.device_id, 10))
    result = owner.run(sock, initial_media=SPEC,
        cancelled=lambda: sock.ons == 1, wait=sock.wait)
    assert sock.ons == sock.offs == sock.states == sock.closed == 1
    assert result.siren.status == 'cancelled' and result.siren.off_confirmed
    assert sock.now < 3


def test_shutdown_before_on_never_emits_an_action():
    sock = Socket()
    owner = worker(sock)
    ticket = owner.siren_inbox.submit(SirenPulseSpec(123, DEVICE.device_id, 2))
    owner.stop()
    owner.run(sock, wait=sock.wait)
    assert sock.ons == sock.offs == 0
    assert not owner.siren_inbox.take(ticket).start_attempted


def test_identity_only_owner_has_no_siren_admission():
    owner = AccountReadWorker(NODE, 1, '192.0.2.1', 1234)
    assert owner.siren_inbox is None


def test_interruption_after_on_still_drains_off_before_propagating(monkeypatch):
    sock = Socket()
    owner = worker(sock)
    owner.siren_inbox.submit(SirenPulseSpec(123, DEVICE.device_id, 10))
    original = sock.recvfrom
    interrupted = []
    def receive(size):
        if sock.ons and not interrupted:
            interrupted.append(True)
            raise KeyboardInterrupt
        return original(size)
    monkeypatch.setattr(sock, 'recvfrom', receive)
    with pytest.raises(KeyboardInterrupt): owner.run(sock, initial_media=SPEC, wait=sock.wait)
    assert sock.ons == sock.offs == sock.states == sock.closed == 1
    assert sock.now < 3


def test_gesture_during_pulse_expires_without_moving_or_killing_stream_owner(monkeypatch):
    sock = Socket()
    owner = AccountReadWorker(NODE, 1, '192.0.2.1', 1234,
        state_reads=True, allow_motion=True, clock=lambda: sock.now)
    owner.siren_inbox.submit(SirenPulseSpec(123, DEVICE.device_id, 2))
    original = sock.recvfrom
    motions = []
    def receive(size):
        if sock.ons and not motions:
            motions.append(owner.motion_inbox.submit(PtzSpec(123, DEVICE.device_id, 'right',frozenset({'right'}))))
        return original(size)
    monkeypatch.setattr(sock, 'recvfrom', receive)
    result = owner.run(sock, cancelled=lambda:sock.now>=5, wait=sock.wait)
    assert result.reason == 'stopped' and result.siren.status == 'completed'
    assert not owner.motion_inbox.take(motions[0]).start_attempted
    assert sock.closes == 0  # No B9/PTZ or media-close command was emitted.
