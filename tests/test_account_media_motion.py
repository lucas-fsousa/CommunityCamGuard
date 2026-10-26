"""Media + reviewed PTZ on synthetic sockets only; never sends to a camera."""

import json
import struct
from dataclasses import replace
from types import SimpleNamespace

import pytest

from backend.app.drivers.yoosee.p2p import account_read_worker as module
from backend.app.drivers.yoosee.p2p.account_policy import AccountPolicy
from backend.app.drivers.yoosee.p2p.cooperative_tcp_media import TcpMediaResult
from backend.app.drivers.yoosee.p2p.crypto import gute_mode2_decrypt, gute_mode2_encrypt
from backend.app.drivers.yoosee.p2p.lamp_read_job import LampReadSpec
from backend.app.drivers.yoosee.p2p.media_policy import MediaPolicy
from backend.app.drivers.yoosee.p2p.ptz_scheduler import PtzSpec
from backend.app.drivers.yoosee.p2p.wire import finish_mode2
from tests.test_account_media_route import SPEC as MEDIA
from tests.test_account_media_route import MediaSocket
from tests.test_broker_reads import DEVICE, NODE, PATH
from tests.test_broker_transactions import ack
from tests.test_lamp_read_job import response as lamp_response
from tests.test_ptz_protocol import reply

MOTION = PtzSpec(123, DEVICE.device_id, "right", frozenset({"right"}))


class Socket(MediaSocket):
    def __init__(self, fault=None):
        super().__init__()
        self.fault_motion = fault
        self.events = []
        self.moves = []
        self.on_start = lambda: None

    def sendto(self, wire, peer):
        plain = gute_mode2_decrypt(wire, NODE.session_key)
        flags = struct.unpack_from("<I", plain, 20)[0]
        if not flags & (1 << 20) and plain[1] == 0xB9:
            if plain[52:56] == b"\x01\xff\0\0":
                value = json.loads(plain[60:])
                assert value["type"] == 2
                pressed = value["data"]["touchType"] == 0
                self.events.append("start" if pressed else "stop")
                self.moves.append((pressed, self.now))
                self.sequences.append(struct.unpack_from("<I", plain, 12)[0])
                if pressed:
                    self.on_start()
                    if self.fault_motion == "send": raise OSError("ambiguous gesture send")
                    if self.fault_motion == "interrupt": raise KeyboardInterrupt
                elif self.fault_motion != "silent":
                    response = bytearray(reply(source=DEVICE.device_id, session=NODE.session_id,
                        sequence=struct.unpack_from("<I", plain, 12)[0],
                        message=struct.unpack_from("<I", plain, 44)[0],
                        request=struct.unpack_from("<I", plain, 56)[0]))
                    struct.pack_into("<I", response, 20, (2 << 16) | (1 << 18))
                    self.queue.append(finish_mode2(response, NODE.session_key))
                return len(wire)
            self.events.append("broker_close")
        if plain[1] == 0xBA:
            self.sequences.append(struct.unpack_from("<I", plain, 12)[0])
            return len(wire)
        return super().sendto(wire, peer)

    def close(self):
        self.events.append("udp_close")
        super().close()


class Tcp:
    def __init__(self, sock, *, ready_after=3, close_fault=False):
        self.sock, self.steps, self.ready_after = sock, 0, ready_after
        self.close_fault = close_fault
        self.result = None

    @property
    def ready(self):
        return self.steps >= self.ready_after and self.result is None

    def step(self):
        self.steps += 1
        self.sock.now += .01

    def finish(self):
        self.sock.events.append("tcp_close")
        self.result = TcpMediaResult(True, not self.close_fault, False, self.steps, 0, 1)
        if self.close_fault:
            raise OSError("unconfirmed TCP cleanup")
        return self.result


def setup(monkeypatch, fault=None, *, ready_after=3, close_fault=False):
    sock = Socket(fault)
    tcp = Tcp(sock, ready_after=ready_after, close_fault=close_fault)
    owner = module.AccountReadWorker(NODE, 1, "192.0.2.1", 1234, allow_motion=True,
        policy=AccountPolicy.CONTINUOUS, clock=lambda: sock.now)
    monkeypatch.setattr(module, "open_tcp_media", lambda *a, **k: tcp)
    return owner, sock, tcp


def run(owner, sock, **kwargs):
    return owner.run(sock, initial_media=MEDIA, with_media_tcp=True,
        media_policy=MediaPolicy.CONTINUOUS, allow_media_motion=True,
        wait=sock.wait, **kwargs)


@pytest.mark.parametrize("direction", ["left", "right", "up", "down"])
def test_movement_read_heartbeat_and_media_use_one_owner_without_starving_video(monkeypatch, direction):
    owner, sock, tcp = setup(monkeypatch)
    ticket = owner.motion_inbox.submit(replace(MOTION, direction=direction, reviewed_directions=frozenset({direction})))
    read = owner.mailbox.submit(DEVICE.device_id, PATH)
    observed = []
    sock.on_start = lambda: observed.append(tcp.ready)
    result = run(owner, sock, cancelled=lambda: sock.now >= 55)
    assert result.reason == "stopped" and result.motion.release_delivery_confirmed
    assert observed == [True] and tcp.steps > 100
    assert [pressed for pressed, _ in sock.moves] == [True, False]
    assert sock.reads == sock.beats == sock.opens == sock.closes == sock.closed == 1
    assert len(sock.sequences) == len(set(sock.sequences)) == 7
    assert owner.motion_inbox.take(ticket).release_delivery_confirmed
    assert owner.mailbox.take(read).status == "response"
    assert sock.events.index("stop") < sock.events.index("tcp_close") < sock.events.index("broker_close")


@pytest.mark.parametrize("fault", ["stop", "drain", "send", "interrupt", "silent", "tcp_close"])
def test_shutdown_stops_motor_before_slow_or_failed_media_teardown(monkeypatch, fault):
    owner, sock, tcp = setup(monkeypatch, fault, close_fault=fault == "tcp_close")
    owner.motion_inbox.submit(MOTION)
    if fault in {"stop", "drain", "silent", "tcp_close"}:
        sock.on_start = owner.drain if fault == "drain" else owner.stop
    if fault == "interrupt":
        with pytest.raises(KeyboardInterrupt): run(owner, sock)
    elif fault == "tcp_close":
        with pytest.raises(OSError, match="TCP cleanup"): run(owner, sock)
    else:
        result = run(owner, sock)
        assert result.motion.release_delivery_confirmed == (fault != "silent")
    assert len([pressed for pressed, _ in sock.moves if pressed]) == 1
    assert sock.events.index("stop") < sock.events.index("tcp_close") < sock.events.index("broker_close")
    assert sock.moves[1][1] - sock.moves[0][1] < .25
    assert sock.closed == 1 and tcp.result is not None and sock.closes == 1


def test_media_not_ready_cannot_start_a_queued_gesture(monkeypatch):
    owner, sock, _tcp = setup(monkeypatch, ready_after=10000)
    ticket = owner.motion_inbox.submit(MOTION)
    run(owner, sock, cancelled=lambda: sock.now >= 2)
    assert not sock.moves
    assert not owner.motion_inbox.take(ticket).start_attempted


def test_mixed_mode_is_not_enabled_by_presence_of_an_inbox(monkeypatch):
    owner, sock, _tcp = setup(monkeypatch)
    result = owner.run(sock, initial_media=MEDIA, with_media_tcp=True,
        media_policy=MediaPolicy.CONTINUOUS, wait=sock.wait)
    assert result.reason == "protocol" and sock.closed == 1
    assert not sock.moves and sock.opens == 0


def test_lamp_reads_and_gesture_share_media_reader_without_duplicate_receipts(monkeypatch):
    class AllSocket(Socket):
        lamp_reads = lamp_acks = 0
        def sendto(self, wire, peer):
            plain = gute_mode2_decrypt(wire, NODE.session_key)
            flags = struct.unpack_from("<I", plain, 20)[0]
            sequence = struct.unpack_from("<I", plain, 12)[0]
            if flags & (1 << 20) and plain[1] == 0xB9 and sequence == 200:
                self.lamp_acks += 1
            if not flags & (1 << 20) and plain[1] == 0xB9 and plain[52:56] == b"\x01\xff\0\0":
                if json.loads(plain[60:])["type"] == 12:
                    self.lamp_reads += 1
                    self.sequences.append(sequence)
                    self.queue.append(finish_mode2(bytearray(ack(sequence, session=NODE.session_id)), NODE.session_key))
                    frame = lamp_response(SimpleNamespace(_request_id=sequence), node=NODE, session=NODE.session_id)
                    self.queue.append(gute_mode2_encrypt(frame, NODE.session_key))
                    return len(wire)
            return super().sendto(wire, peer)
    sock = AllSocket()
    tcp = Tcp(sock)
    monkeypatch.setattr(module, "open_tcp_media", lambda *a, **k: tcp)
    owner = module.AccountReadWorker(NODE, 1, "192.0.2.1", 1234, allow_motion=True,
        state_reads=True, policy=AccountPolicy.CONTINUOUS, clock=lambda: sock.now)
    motion = owner.motion_inbox.submit(MOTION)
    lamp = owner.lamp_inbox.submit(LampReadSpec(123, DEVICE.device_id))
    read = owner.mailbox.submit(DEVICE.device_id, PATH)
    result = run(owner, sock, cancelled=lambda: sock.now >= 55)
    assert result.motion.release_delivery_confirmed and result.media.broker_release
    assert owner.lamp_inbox.take(lamp).status == "response"
    assert owner.mailbox.take(read).status == "response"
    assert owner.motion_inbox.take(motion).release_delivery_confirmed
    assert sock.lamp_reads == sock.lamp_acks == 1 and sock.beats == sock.closed == 1
    assert tcp.steps > 100 and len(sock.sequences) == len(set(sock.sequences)) == 9
