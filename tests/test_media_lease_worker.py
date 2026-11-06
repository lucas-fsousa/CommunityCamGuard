"""Dynamic sources on a synthetic broker; no device, sound or light I/O."""

import pytest

from backend.app.drivers.yoosee.p2p.account_policy import AccountPolicy
from backend.app.drivers.yoosee.p2p.account_read_worker import AccountReadWorker
from backend.app.drivers.yoosee.p2p.cooperative_tcp_media import TcpMediaResult
from backend.app.drivers.yoosee.p2p.media_lease_inbox import MediaLeaseInbox
from backend.app.drivers.yoosee.p2p.media_policy import MediaPolicy
from tests.test_account_media_route import MediaSocket
from tests.test_broker_reads import NODE
from tests.test_media_lease_inbox import TARGETS, bootstrap, camera


class Tcp:
    def __init__(self, video):
        self.video, self.result = video, None
        self.steps = self.closed = 0
        self.closing = False

    @property
    def ready(self):
        return self.result is None and not self.closing

    def begin_finish(self):
        self.closing = True

    def step(self, *, budget_seconds=0.2):
        assert 0 < budget_seconds <= 0.2
        self.steps += 1
        if self.closing:
            self.finish()

    def finish(self):
        if self.result is None:
            self.closed += 1
            self.video.close()
            self.result = TcpMediaResult(True, True, False, self.steps, 0, 0)
        return self.result


def setup(monkeypatch):
    sock, inbox, pumps = MediaSocket(), MediaLeaseInbox(1, 123, targets=TARGETS), []
    owner = AccountReadWorker(NODE, 1, "192.0.2.1", 1234, clock=lambda: sock.now,
                              policy=AccountPolicy.CONTINUOUS, media_capacity=3)
    def opener(route, spec, *, clock, video, policy):
        assert route.endpoint is not None and policy is MediaPolicy.CONTINUOUS
        tcp = Tcp(video)
        pumps.append(tcp)
        return tcp
    monkeypatch.setattr("backend.app.drivers.yoosee.p2p.account_read_worker.open_tcp_media", opener)
    return sock, inbox, pumps, owner


def execute(sock, inbox, owner, duration=0.2):
    return owner.run(sock, media_leases=inbox, with_media_tcp=True,
        media_policy=MediaPolicy.CONTINUOUS, cancelled=lambda: sock.now >= duration, wait=sock.wait)


def test_attach_detach_replace_one_source_while_sibling_keeps_streaming(monkeypatch):
    sock, inbox, pumps, owner = setup(monkeypatch)
    first = inbox.submit(camera(0), bootstrap(0))
    second = inbox.submit(camera(1), bootstrap(1))
    receipts, replacements = [], []
    receive = sock.recvfrom
    def poll(size):
        if sock.now >= 0.02:
            inbox.cancel(first)
        if sock.now >= 0.04 and not replacements:
            receipt = inbox.take(first)
            if receipt is not None:
                receipts.append(receipt)
                replacements.append(inbox.submit(camera(0), bootstrap(0)))
                assert not inbox.cancel(first)
                assert not inbox.should_cancel(replacements[0])
        return receive(size)
    monkeypatch.setattr(sock, "recvfrom", poll)
    result = execute(sock, inbox, owner)
    assert result.reason == "stopped" and not result.lease_cleanup_uncertain
    assert receipts[0].status == "closed"
    assert len(replacements) == 1 and len(pumps) == 3
    assert pumps[1].steps > pumps[0].steps * 3
    assert all(tcp.closed == 1 and tcp.video.closed for tcp in pumps)
    assert inbox.take(second).status == inbox.take(replacements[0]).status == "closed"
    assert sock.opens == sock.closes == 3 and sock.closed == 1
    assert len(sock.sequences) == len(set(sock.sequences))


def test_idle_account_accepts_a_later_source_without_another_login(monkeypatch):
    sock, inbox, pumps, owner = setup(monkeypatch)
    submitted = []
    receive = sock.recvfrom
    def poll(size):
        if sock.now >= 0.05 and not submitted:
            submitted.append(inbox.submit(camera(0), bootstrap(0)))
        return receive(size)
    monkeypatch.setattr(sock, "recvfrom", poll)
    result = execute(sock, inbox, owner)
    assert result.reason == "stopped" and len(pumps) == 1
    assert inbox.take(submitted[0]).status == "closed"
    assert sock.opens == sock.closes == sock.closed == 1


def test_cancel_before_claim_never_opens_camera_route(monkeypatch):
    sock, inbox, pumps, owner = setup(monkeypatch)
    ticket = inbox.submit(camera(0), bootstrap(0))
    inbox.cancel(ticket)
    result = execute(sock, inbox, owner)
    assert result.reason == "stopped" and not pumps
    assert inbox.take(ticket).status == "cancelled"
    assert sock.opens == sock.closes == 0 and sock.closed == 1


def test_missing_close_receipt_fences_dynamic_source_replacement(monkeypatch):
    sock, inbox, pumps, owner = setup(monkeypatch)
    ticket = inbox.submit(camera(0), bootstrap(0))
    sock.media_fault = "silent"
    receive = sock.recvfrom
    def poll(size):
        if sock.now >= 0.02:
            inbox.cancel(ticket)
        return receive(size)
    monkeypatch.setattr(sock, "recvfrom", poll)
    result = execute(sock, inbox, owner, duration=2)
    assert result.reason == "transport" and result.lease_cleanup_uncertain
    assert inbox.take(ticket).status == "blocked"
    with pytest.raises(ValueError):
        inbox.submit(camera(0), bootstrap(0))
    assert len(pumps) == sock.opens == sock.closes == sock.closed == 1
