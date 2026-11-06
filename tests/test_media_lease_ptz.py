"""Motor/lease ordering on synthetic encrypted replies. No real camera actions."""

import json
import struct

from backend.app.drivers.yoosee.p2p.account_policy import AccountPolicy
from backend.app.drivers.yoosee.p2p.account_read_worker import AccountReadWorker
from backend.app.drivers.yoosee.p2p.crypto import gute_mode2_decrypt
from backend.app.drivers.yoosee.p2p.media_lease_inbox import MediaLeaseInbox
from backend.app.drivers.yoosee.p2p.media_policy import MediaPolicy
from backend.app.drivers.yoosee.p2p.ptz_scheduler import PtzSpec
from backend.app.drivers.yoosee.p2p.wire import finish_mode2
from tests.test_account_media_route import MediaSocket
from tests.test_broker_reads import NODE
from tests.test_media_lease_inbox import TARGETS, bootstrap, camera
from tests.test_media_lease_worker import Tcp
from tests.test_ptz_protocol import reply


class MotionMediaSocket(MediaSocket):
    def __init__(self, *, silent=False):
        super().__init__()
        self.events, self.moves = [], []
        self.on_start = lambda: None
        self.silent = silent

    def sendto(self, wire, peer):
        plain = gute_mode2_decrypt(wire, NODE.session_key)
        flags = struct.unpack_from("<I", plain, 20)[0]
        if plain[1] == 0xB9 and not flags & (1 << 20) and plain[60:61] == b"{":
            body = json.loads(plain[60:])
            touch = body["data"]["touchType"]
            device = struct.unpack_from("<Q", plain, 28)[0]
            self.events.append(("start" if touch == 0 else "stop", device))
            self.moves.append(wire)
            if touch == 0:
                self.on_start()
            elif not self.silent:
                frame = bytearray(reply(session=NODE.session_id,
                    sequence=struct.unpack_from("<I", plain, 12)[0], source=device, destination=123,
                    message=struct.unpack_from("<I", plain, 44)[0], request=struct.unpack_from("<I", plain, 56)[0]))
                struct.pack_into("<I", frame, 20, (2 << 16) | (1 << 18))
                self.queue.append(finish_mode2(frame, NODE.session_key))
            return len(wire)
        if plain[1] == 0xBA:
            self.events.append(("stop_receipt", struct.unpack_from("<Q", plain, 28)[0]))
            return len(wire)
        if plain[1] == 0xB9 and not flags & (1 << 20):
            self.events.append(("broker_close", None))
        return super().sendto(wire, peer)


def run(monkeypatch, *, cancel_target=0, silent=False, motion_device=100):
    sock, inbox, pumps = MotionMediaSocket(silent=silent), MediaLeaseInbox(1, 123, targets=TARGETS), []
    tickets = [inbox.submit(camera(index), bootstrap(index)) for index in range(2)]
    owner = AccountReadWorker(NODE, 1, "192.0.2.1", 1234, clock=lambda: sock.now,
                              policy=AccountPolicy.CONTINUOUS, media_capacity=3, allow_motion=True)
    class TrackedTcp(Tcp):
        def begin_finish(self):
            sock.events.append(("tcp_close_begin", self.device))
            super().begin_finish()
    def opener(route, spec, *, clock, video, policy):
        pump = TrackedTcp(video)
        pump.device = spec.device.device_id
        pumps.append(pump)
        return pump
    monkeypatch.setattr("backend.app.drivers.yoosee.p2p.account_read_worker.open_tcp_media", opener)
    sock.on_start = lambda: inbox.cancel(tickets[cancel_target])
    receive, submitted = sock.recvfrom, []
    def poll(size):
        if len(pumps) == 2 and not submitted:
            submitted.append(owner.motion_inbox.submit(PtzSpec(123, motion_device, "right", frozenset({"right"}))))
        return receive(size)
    monkeypatch.setattr(sock, "recvfrom", poll)
    result = owner.run(sock, media_leases=inbox, with_media_tcp=True, media_policy=MediaPolicy.CONTINUOUS,
        allow_media_motion=True, cancelled=lambda: sock.now >= (3 if silent else 0.5), wait=sock.wait)
    return sock, inbox, tickets, pumps, owner, submitted, result


def test_stop_is_confirmed_before_same_camera_tcp_detach(monkeypatch):
    sock, inbox, tickets, pumps, _, _, result = run(monkeypatch)
    assert result.reason == "stopped" and result.motion.release_delivery_confirmed
    assert sock.events.index(("stop_receipt", 100)) < sock.events.index(("tcp_close_begin", 100))
    assert sock.events.index(("tcp_close_begin", 100)) < next(i for i, item in enumerate(sock.events) if item[0] == "broker_close")
    assert pumps[1].steps > 100 and len(sock.moves) == 2
    assert inbox.take(tickets[0]).status == inbox.take(tickets[1]).status == "closed"
    assert not result.lease_cleanup_uncertain and sock.closed == 1


def test_stopping_a_sibling_does_not_cancel_the_other_camera_motion(monkeypatch):
    sock, inbox, tickets, _, _, _, result = run(monkeypatch, cancel_target=1)
    assert result.motion.release_delivery_confirmed
    assert sock.events.index(("tcp_close_begin", 101)) < sock.events.index(("stop", 100))
    assert inbox.take(tickets[1]).status == "closed" and len(sock.moves) == 2


def test_unconfirmed_motor_stop_blocks_all_lease_outcomes(monkeypatch):
    sock, inbox, tickets, _, _, _, result = run(monkeypatch, silent=True)
    assert result.reason == "transport" and result.lease_cleanup_uncertain
    assert result.motion.start_attempted and not result.motion.release_delivery_confirmed
    assert len(sock.moves) == 4 and sock.moves[1] == sock.moves[2] == sock.moves[3]
    assert all(inbox.take(ticket).status == "blocked" for ticket in tickets)
    assert sock.closed == 1


def test_motion_for_an_unleased_device_never_starts(monkeypatch):
    sock, _, _, pumps, owner, submitted, result = run(monkeypatch, motion_device=102)
    assert result.reason == "stopped" and not sock.moves
    assert owner.motion_inbox.take(submitted[0]).start_attempted is False
    assert all(pump.steps > 100 for pump in pumps)
