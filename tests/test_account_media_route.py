"""Single account reader, synthetic encrypted broker; never opens a media socket."""

import struct

import pytest

from backend.app.drivers.yoosee.p2p.account_output_budget import (
    AccountOutputBudget,
    OutputExhausted,
)
from backend.app.drivers.yoosee.p2p.account_read_lifecycle import AccountReadLifecycle
from backend.app.drivers.yoosee.p2p.account_read_worker import AccountReadWorker
from backend.app.drivers.yoosee.p2p.broker_sequences import BrokerSequences
from backend.app.drivers.yoosee.p2p.crypto import gute_mode2_decrypt
from backend.app.drivers.yoosee.p2p.media_route_job import MediaRouteJob, MediaRouteSpec
from backend.app.drivers.yoosee.p2p.wire import finish_mode2
from tests.test_account_read_lifecycle import connection
from tests.test_account_read_worker import WorkerSocket, submit, worker
from tests.test_broker_reads import DEVICE, NODE
from tests.test_broker_transactions import ack
from tests.test_tcp_route import advertisement

SPEC = MediaRouteSpec(123, DEVICE)


class MediaSocket(WorkerSocket):
    def __init__(self, media_fault=None):
        super().__init__()
        self.media_fault = media_fault
        self.opens = self.closes = 0
        self.sequences = []

    def wait(self, seconds): self.now += seconds

    def sendto(self, wire, peer):
        plain = gute_mode2_decrypt(wire, NODE.session_key)
        flags = struct.unpack_from("<I", plain, 20)[0]
        sequence = struct.unpack_from("<I", plain, 12)[0]
        if not flags & (1 << 20): self.sequences.append(sequence)
        if plain[1] == 0xA4:
            self.opens += 1
            if self.media_fault == "open": raise OSError("ambiguous open")
            if self.media_fault == "interrupt": raise KeyboardInterrupt
            if self.media_fault != "advert":
                frame = bytearray(advertisement(link=struct.unpack_from("<I", plain, 28)[0]))
                struct.pack_into("<Q", frame, 4, NODE.session_id)
                self.queue.append(finish_mode2(frame, NODE.session_key))
            return len(wire)
        if plain[1] == 0xB9 and not flags & (1 << 20):
            self.closes += 1
            if self.media_fault == "close_send": raise OSError("ambiguous close")
            if self.media_fault != "silent":
                frame = ack(sequence, session=NODE.session_id,
                            result=1 if self.media_fault == "negative" else 0)
                self.queue.append(finish_mode2(bytearray(frame), NODE.session_key))
            return len(wire)
        return super().sendto(wire, peer)


def test_route_reads_and_heartbeat_share_sequence_namespace_and_socket():
    sock = MediaSocket()
    owner = worker(sock)
    ticket = submit(owner)
    result = owner.run(sock, initial_media=SPEC, cancelled=lambda: sock.now >= 55, wait=sock.wait)
    assert result.reason == "stopped" and result.media.broker_release
    assert sock.opens == sock.closes == sock.reads == sock.beats == sock.closed == 1
    assert len(sock.sequences) == len(set(sock.sequences)) == 4
    assert owner.mailbox.take(ticket).status == "response"


@pytest.mark.parametrize("fault,confirmed", [("open", True), ("advert", True),
    ("silent", False), ("close_send", False), ("negative", False)])
def test_failures_close_once_never_replay_open(fault, confirmed):
    sock = MediaSocket(fault)
    result = worker(sock).run(sock, initial_media=SPEC,
                              cancelled=lambda: sock.now >= 6, wait=sock.wait)
    assert result.media.opened and result.media.close_attempted
    assert result.media.broker_release is confirmed
    assert sock.opens == sock.closes == sock.closed == 1
    assert sock.now < 8


def test_stop_before_start_never_opens_route():
    sock = MediaSocket()
    owner = worker(sock)
    owner.stop()
    result = owner.run(sock, initial_media=SPEC, wait=sock.wait)
    assert not result.media.opened and sock.opens == sock.closes == 0 and sock.closed == 1


def test_unconfirmed_route_release_blocks_generation_replacement(monkeypatch):
    sock = MediaSocket("silent")
    lifecycle = AccountReadLifecycle(clock=lambda: sock.now)
    original = AccountReadWorker.run
    def run(self, transport, **kwargs):
        return original(self, transport, initial_media=SPEC, wait=sock.wait, **kwargs)
    monkeypatch.setattr(AccountReadWorker, "run", run)
    result = lifecycle.run(lambda _: connection(sock), cancelled=lambda: sock.now >= 1)
    assert not result.media.broker_release and lifecycle.state == "blocked"
    with pytest.raises(ValueError):
        lifecycle.run(lambda _: pytest.fail("replacement authentication"))


def test_media_and_ptz_cleanup_capacity_cannot_be_borrowed():
    output = AccountOutputBudget(8, 8 * 4096)
    media = output.reserve_media()
    ptz = output.reserve_ptz()
    with pytest.raises(OutputExhausted): output.charge(1)
    media.charge("open", 4096)
    media.charge("close", 4096)
    with pytest.raises(OutputExhausted): output.charge(1)
    ptz.charge("start", 4096)
    for _ in range(3): ptz.charge("release", 4096)
    for _ in range(2): ptz.charge("receipt", 4096)
    with pytest.raises(OutputExhausted): output.charge(1)


@pytest.mark.parametrize("fault", ["sequence", "session", "kind", "generation", "peer", "deadline"])
def test_close_receipt_must_match_exact_old_route(fault):
    now = [0.0]
    source = BrokerSequences(NODE.session_id, 1, 40)
    job = MediaRouteJob(NODE, source, AccountOutputBudget(), SPEC, generation=1,
                        local_ip="192.0.2.1", local_port=1234, clock=lambda: now[0])
    assert job.next_packet().kind == "open"
    job.cancel()
    source.close()  # Retiring the account cannot invalidate its preallocated cleanup.
    assert job.next_packet().kind == "close"
    frame = ack(42 if fault == "sequence" else 41,
                session=NODE.session_id + (fault == "session"),
                kind=0xB7 if fault == "kind" else 0xB9)
    if fault == "deadline": now[0] = 1.5
    job.observe(frame, ("192.0.2.99", 1) if fault == "peer" else NODE.address,
                generation=2 if fault == "generation" else 1)
    assert job.result is None
    now[0] = 1.5
    job.next_packet()
    assert not job.result.broker_release


def test_interrupted_open_still_attempts_close_on_old_socket():
    sock = MediaSocket("interrupt")
    owner = worker(sock)
    with pytest.raises(KeyboardInterrupt):
        owner.run(sock, initial_media=SPEC, wait=sock.wait)
    assert sock.opens == sock.closes == sock.closed == 1 and owner._node is None


def test_sequence_exhaustion_reserves_nothing_and_never_wraps():
    source = BrokerSequences(NODE.session_id, 1, (1 << 32) - 1)
    output = AccountOutputBudget()
    with pytest.raises(ValueError, match="exhausted"):
        MediaRouteJob(NODE, source, output, SPEC, generation=1,
                      local_ip="192.0.2.1", local_port=1234)
    assert source.allocate() == (1 << 32) - 1
    output.reserve_media().close()
