"""Paired TCP protocol fixtures and shared UDP worker, no real sockets/cameras."""

import pytest

from backend.app.drivers.yoosee.p2p import account_read_worker as worker_module
from backend.app.drivers.yoosee.p2p import cooperative_tcp_media as module
from backend.app.drivers.yoosee.p2p.cooperative_tcp_media import CooperativeTcpMedia
from backend.app.drivers.yoosee.p2p.kcp_receive import ReceiveError
from tests.test_account_media_route import SPEC, MediaSocket
from tests.test_account_read_worker import submit
from tests.test_broker_reads import NODE
from tests.test_media_receive import PEER
from tests.test_mtp_tcp_receive import Socket
from tests.test_vendor_mtp_tcp_av import session


class OwnedSocket(Socket):
    def __init__(self, now, fault=None):
        super().__init__(now, fault)
        self.closed = 0

    def close(self):
        self.closed += 1
        if self.fault == "physical_close": raise OSError("close unknown")


def test_finite_pump_consumes_records_without_second_reader_or_history():
    now = [1.0]
    sock, owner = OwnedSocket(now), session(now)
    pump = CooperativeTcpMedia(sock, PEER, owner, clock=lambda: now[0])
    steps = 0
    while pump.result is None:
        pump.step()
        steps += 1
        assert steps < 200
    result = pump.finish()
    assert result.ready and result.close_acknowledged and not result.socket_close_failed
    assert (result.video_frames, result.audio_frames, result.encoding_headers) == (1, 2, 1)
    assert sock.actions == [1, 6, 7] and sock.closed == 1
    assert owner.buffered_bytes == 0 and owner.closed


def test_explicit_shutdown_drains_avclose_before_physical_close():
    now = [1.0]
    sock, owner = OwnedSocket(now), session(now)
    pump = CooperativeTcpMedia(sock, PEER, owner, clock=lambda: now[0])
    while not owner.ready: pump.step()
    result = pump.finish()
    assert result.close_acknowledged and sock.actions[-1] == 7 and sock.closed == 1
    pump.finish()
    assert sock.closed == 1


@pytest.mark.parametrize("fault", ["send", "eof", "peer", "close", "physical_close"])
def test_failure_is_terminal_and_close_is_not_retried(fault):
    now = [1.0]
    sock, owner = OwnedSocket(now, fault), session(now)
    pump = CooperativeTcpMedia(sock, PEER, owner, clock=lambda: now[0])
    try:
        while pump.result is None: pump.step()
    except (OSError, ReceiveError):
        pass
    result = pump.finish()
    assert sock.closed == 1 and owner.closed and owner.buffered_bytes == 0
    assert not result.close_acknowledged or result.socket_close_failed


@pytest.mark.parametrize("cancel", [False, True])
def test_shared_worker_orders_avclose_tcp_close_then_broker_release(monkeypatch, cancel):
    now = [1.0]
    udp = MediaSocket()
    tcp = OwnedSocket(now)
    events = []
    tcp_owner = session(now)
    pump = CooperativeTcpMedia(tcp, PEER, tcp_owner, clock=lambda: now[0])
    original_receive = tcp.recv
    def fast_receive(size):
        try: return original_receive(size)
        finally: now[0] -= 0.049  # Fast fragmented TCP; UDP polling also advances time.
    monkeypatch.setattr(tcp, "recv", fast_receive)
    original_udp_receive = udp.recvfrom
    def receive(size):
        udp.now = now[0]
        try: return original_udp_receive(size)
        finally: now[0] = udp.now
    monkeypatch.setattr(udp, "recvfrom", receive)
    def opening(job, spec, **kwargs):
        assert job.endpoint is not None and spec == SPEC
        events.append("tcp_open")
        return pump
    monkeypatch.setattr(worker_module, "open_tcp_media", opening)
    original_close = tcp.close
    def closing():
        assert tcp_owner.close_acknowledged is False  # Parser already retired; result retains receipt.
        events.append("tcp_close")
        original_close()
    monkeypatch.setattr(tcp, "close", closing)
    original_send = udp.sendto
    def sending(wire, peer):
        if wire[1] == 0xB9:
            assert tcp.closed == 1 and tcp.actions[-1] == 7
            events.append("broker_close")
        return original_send(wire, peer)
    monkeypatch.setattr(udp, "sendto", sending)
    owner = worker_module.AccountReadWorker(NODE, 1, "192.0.2.1", 1234, clock=lambda: now[0])
    ticket = submit(owner)
    result = owner.run(udp, initial_media=SPEC, with_media_tcp=True,
        cancelled=lambda: cancel and tcp_owner.ready, wait=lambda seconds: None)
    assert result.reason == ("stopped" if cancel else "media_complete")
    assert result.tcp.close_acknowledged and result.media.broker_release
    assert owner.mailbox.take(ticket).status == "response"
    assert events == ["tcp_open", "tcp_close", "broker_close"]
    assert udp.opens == udp.closes == udp.closed == 1


@pytest.mark.parametrize("fault", [None, "connect", "pair", "close"])
def test_opening_pairs_once_and_owns_cleanup(monkeypatch, fault):
    from types import SimpleNamespace

    from tests.test_av_receive import COOKIE

    calls = []
    class OpeningSocket:
        def settimeout(self, seconds):
            assert 0 < seconds <= 0.5
        def connect(self, peer):
            assert peer == PEER
            calls.append("connect")
            if fault == "connect": raise OSError("connect failed")
        def sendall(self, wire):
            calls.append("pair")
            if fault in ("pair", "close"): raise OSError("pair failed")
        def close(self):
            calls.append("close")
            if fault == "close": raise OSError("close unknown")
    monkeypatch.setattr(module.socket, "socket", lambda *args: OpeningSocket())
    job = SimpleNamespace(endpoint=PEER, result=None, metadata=None,
                          attempt=SimpleNamespace(link_id=42, call_id=123, cookie=COOKIE))
    if fault:
        with pytest.raises(module.P2PProbeError if fault == "close" else OSError):
            module.open_tcp_media(job, SPEC)
        assert calls.count("close") == 1 and calls.count("connect") == 1
    else:
        pump = module.open_tcp_media(job, SPEC)
        result = pump.finish()  # Before negotiation: no fabricated AVCLOSE receipt.
        assert not result.close_acknowledged and not result.socket_close_failed
        assert calls == ["connect", "pair", "close"]


def test_missing_avclose_receipt_blocks_new_generation_even_with_broker_receipt(monkeypatch):
    from backend.app.drivers.yoosee.p2p.account_read_lifecycle import AccountReadLifecycle
    from backend.app.drivers.yoosee.p2p.media_route_job import MediaRouteResult
    from tests.test_account_read_lifecycle import connection

    summary = worker_module.WorkerSummary("protocol", 2, 2, 0, False,
        media=MediaRouteResult(True, True, True),
        tcp=module.TcpMediaResult(True, False, False, 1, 0, 1))
    monkeypatch.setattr(worker_module.AccountReadWorker, "run", lambda *a, **k: summary)
    lifecycle = AccountReadLifecycle()
    lifecycle.run(lambda _: connection(MediaSocket()))
    assert lifecycle.state == "blocked"
    with pytest.raises(ValueError):
        lifecycle.run(lambda _: pytest.fail("unsafe replacement"))
