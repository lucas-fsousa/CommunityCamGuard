import json
import struct
import threading

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


def test_other_thread_stop_does_not_close_socket_before_release():
    sock = MotionSocket()
    owner = worker(sock)
    started = threading.Event()
    stopped = threading.Event()
    def stop_from_caller():
        assert started.wait(2)
        owner.stop()
        stopped.set()
    thread = threading.Thread(target=stop_from_caller)
    def on_start():
        started.set()
        assert stopped.wait(2)
        assert not sock.closed
    sock.on_start = on_start
    thread.start()
    try:
        result = owner.run(sock, initial_motion=SPEC, wait=sock.wait)
    finally:
        started.set()
        thread.join(timeout=2)
    assert not thread.is_alive()
    assert result.reason == "stopped" and result.motion.release_delivery_confirmed
    assert len(sock.moves) == 2 and sock.closed == 1


def test_input_flood_retires_without_consuming_reserved_stop(monkeypatch):
    sock = MotionSocket("silent")
    owner = worker(sock)
    def flood(size):
        assert size == 4097
        # Bounded packets may arrive faster than the monotonic clock advances.
        sock.now += 0.0001
        return b"noise", NODE.address
    monkeypatch.setattr(sock, "recvfrom", flood)
    result = owner.run(sock, initial_motion=SPEC, wait=sock.wait)
    assert result.reason == "protocol" and not result.motion.release_delivery_confirmed
    assert len(sock.moves) == 4 and sock.closed == 1
    assert result.received_datagrams == 513 + 65
    assert sock.now < 2.2


def test_unread_motion_failure_does_not_replay_reads_during_cleanup():
    sock = MotionSocket("send")
    owner = worker(sock)
    ticket = submit(owner)
    result = owner.run(sock, initial_motion=SPEC, wait=sock.wait)
    assert result.motion.release_delivery_confirmed and result.motion.send_failed
    assert owner.mailbox.take(ticket).status == "cancelled"
    assert not sock.reads and not sock.beats and sock.closed == 1


def test_typed_inbox_reuses_account_before_and_after_heartbeat(monkeypatch):
    sock = MotionSocket()
    owner = AccountReadWorker(NODE, 1, "192.0.2.1", 1234,
                              clock=lambda: sock.now, allow_motion=True)
    inbox = owner.motion_inbox
    first = inbox.submit(SPEC)
    ticket = submit(owner)
    observed = []
    second = []
    receive = sock.recvfrom
    def receiving(size):
        if not observed:
            result = inbox.take(first)
            if result is not None: observed.append(result)
        if observed and sock.beats and not second:
            second.append(inbox.submit(SPEC))
            assert not inbox.cancel(first)  # stale UI cancellation cannot hit gesture2
        return receive(size)
    monkeypatch.setattr(sock, "recvfrom", receiving)
    result = owner.run(sock, wait=sock.wait)
    assert result.reason == "deadline" and result.motion.release_delivery_confirmed
    assert observed[0].release_delivery_confirmed
    assert inbox.take(second[0]).release_delivery_confirmed
    assert len(sock.moves) == 4 and sock.reads == sock.beats == 1 and sock.closed == 1
    assert owner.mailbox.take(ticket).status == "response"
    sequences = [struct.unpack_from("<I", gute_mode2_decrypt(wire, NODE.session_key), 12)[0]
                 for wire in sock.moves]
    assert len(set(sequences)) == 4


def test_active_inbox_cancel_waits_for_stop_result():
    sock = MotionSocket()
    owner = AccountReadWorker(NODE, 1, "192.0.2.1", 1234,
                              clock=lambda: sock.now, allow_motion=True)
    inbox = owner.motion_inbox
    ticket = inbox.submit(SPEC)
    def cancel():
        assert inbox.cancel(ticket)
        assert inbox.take(ticket) is None
    sock.on_start = cancel
    result = owner.run(sock, wait=sock.wait)
    assert result.motion.release_delivery_confirmed
    assert inbox.take(ticket).release_delivery_confirmed and len(sock.moves) == 2


def test_inbox_unknown_stop_prevents_further_motion():
    sock = MotionSocket("silent")
    owner = AccountReadWorker(NODE, 1, "192.0.2.1", 1234,
                              clock=lambda: sock.now, allow_motion=True)
    inbox = owner.motion_inbox
    ticket = inbox.submit(SPEC)
    result = owner.run(sock, wait=sock.wait)
    assert result.reason == "transport" and not result.motion.release_delivery_confirmed
    assert inbox.take(ticket).reason == "release_unconfirmed"
    with pytest.raises(ValueError): inbox.submit(SPEC)
    assert len(sock.moves) == 4 and sock.closed == 1
