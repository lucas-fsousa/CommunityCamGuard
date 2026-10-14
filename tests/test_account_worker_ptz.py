import json
import struct

import pytest

from backend.app.drivers.yoosee.p2p.account_read_lifecycle import AccountReadLifecycle
from backend.app.drivers.yoosee.p2p.account_read_worker import AccountReadWorker
from backend.app.drivers.yoosee.p2p.crypto import gute_mode2_decrypt
from backend.app.drivers.yoosee.p2p.ptz_scheduler import PtzSpec
from backend.app.drivers.yoosee.p2p.wire import finish_mode2
from tests.test_account_read_lifecycle import connection
from tests.test_account_read_worker import WorkerSocket, submit, worker
from tests.test_broker_reads import NODE
from tests.test_ptz_protocol import reply

SPEC = PtzSpec(123, 456, "right", frozenset({"right"}))


class MotionSocket(WorkerSocket):
    def __init__(self, motion_fault=None):
        super().__init__()
        self.motion_fault = motion_fault
        self.moves = []
        self.receipts = 0
        self.on_start = lambda: None

    def sendto(self, wire, peer):
        assert not self.closed
        plain = gute_mode2_decrypt(wire, NODE.session_key)
        flags = struct.unpack_from("<I", plain, 20)[0]
        if plain[1] == 0xBA:
            self.receipts += 1
            return len(wire)
        if plain[1] != 0xB9 or flags & (1 << 20):
            return super().sendto(wire, peer)
        payload = json.loads(plain[60:])
        pressed = payload["data"]["touchType"] == 0
        self.moves.append(wire)
        if pressed:
            self.on_start()
            if self.motion_fault == "send": raise OSError("ambiguous start")
            if self.motion_fault == "interrupt": raise KeyboardInterrupt
        elif self.motion_fault != "silent":
            response = bytearray(reply(session=NODE.session_id,
                sequence=struct.unpack_from("<I", plain, 12)[0],
                message=struct.unpack_from("<I", plain, 44)[0],
                request=struct.unpack_from("<I", plain, 56)[0]))
            struct.pack_into("<I", response, 20, (2 << 16) | (1 << 18))
            self.queue.append(finish_mode2(response, NODE.session_key))
        return len(wire)

    def wait(self, seconds): self.now += seconds


def test_one_socket_serves_motion_reads_and_heartbeat_without_duplicate_ack():
    sock = MotionSocket()
    owner = worker(sock)
    ticket = submit(owner)
    result = owner.run(sock, initial_motion=SPEC, wait=sock.wait)
    assert result.reason == "deadline" and result.motion.release_delivery_confirmed
    assert len(sock.moves) == 2 and sock.receipts == 1
    assert sock.reads == sock.beats == 1 and sock.acks == 3
    assert owner.mailbox.take(ticket).status == "response"
    assert sock.closed == 1


@pytest.mark.parametrize("fault", ["stop", "drain", "send", "interrupt", "silent"])
def test_shutdown_drains_old_socket_without_start_replay(fault):
    sock = MotionSocket(fault)
    owner = worker(sock)
    if fault == "stop": sock.on_start = owner.stop
    if fault == "drain": sock.on_start = owner.drain
    if fault == "interrupt":
        with pytest.raises(KeyboardInterrupt):
            owner.run(sock, initial_motion=SPEC, wait=sock.wait)
    else:
        result = owner.run(sock, initial_motion=SPEC, wait=sock.wait)
        assert result.motion.release_delivery_confirmed == (fault != "silent")
        if fault == "send": assert result.motion.send_failed
    assert sock.closed == 1
    assert len(sock.moves) == (4 if fault == "silent" else 2)
    assert sock.now < 2.4


def test_prestart_stop_sends_no_motion():
    sock = MotionSocket()
    owner = worker(sock)
    owner.stop()
    result = owner.run(sock, initial_motion=SPEC, wait=sock.wait)
    assert not sock.moves and not result.motion.start_attempted and sock.closed == 1


def test_receive_failure_keeps_stop_reserve_and_closes_after_bounded_drain():
    sock = MotionSocket()
    sock.fault = "receive"
    owner = worker(sock)
    result = owner.run(sock, initial_motion=SPEC, wait=sock.wait)
    assert result.reason == "socket" and not result.motion.release_delivery_confirmed
    assert len(sock.moves) == result.sent_datagrams == 4
    assert sock.moves[1] == sock.moves[2] == sock.moves[3]
    assert sock.closed == 1 and 2 <= sock.now < 2.1


@pytest.mark.parametrize("silent", [False, True])
def test_lifecycle_blocks_new_auth_when_stop_is_unknown(monkeypatch, silent):
    sock = MotionSocket("silent" if silent else None)
    lifecycle = AccountReadLifecycle(clock=lambda: sock.now)
    original = AccountReadWorker.run
    def run(self, transport, **kwargs):
        return original(self, transport, initial_motion=SPEC, wait=sock.wait, **kwargs)
    monkeypatch.setattr(AccountReadWorker, "run", run)
    summary = lifecycle.run(lambda _: connection(sock))
    assert summary.motion.release_delivery_confirmed == (not silent)
    assert lifecycle.state == ("blocked" if silent else "idle")
    if silent:
        with pytest.raises(ValueError):
            lifecycle.run(lambda _: pytest.fail("unexpected reauthentication"))
