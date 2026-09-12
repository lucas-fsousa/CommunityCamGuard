"""Prepared native route tests with synthetic UDP; never contact a camera."""
from collections import deque
from types import SimpleNamespace

import pytest

from backend.app.drivers.yoosee.p2p import ptz_route
from backend.app.drivers.yoosee.p2p.wire import finish_mode2
from tests.test_ptz_protocol import NODE, reply


class Socket:
    def __init__(self):
        self.sent = []
        self.incoming = deque()
        self.closed = 0
        self.received = 0

    def setblocking(self, value):
        assert value is False

    def sendto(self, wire, peer):
        self.sent.append((wire, peer))

    def recvfrom(self, limit):
        assert limit == 4097
        self.received += 1
        return self.incoming.popleft()

    def close(self):
        self.closed += 1


@pytest.fixture
def prepared(monkeypatch):
    ids = iter((100, 200, 101, 201))
    monkeypatch.setattr(ptz_route, "secrets", SimpleNamespace(randbits=lambda _: next(ids)))
    monkeypatch.setattr(ptz_route.time, "monotonic", lambda: 100)
    sock = Socket()
    monkeypatch.setattr(ptz_route.select, "select", lambda *args: ([sock] if sock.incoming else [], [], []))
    route = ptz_route.NativePtzRoute(sock, NODE, access_id=123, device_id=456, direction="right", sequence=18)
    return route, sock


def packet(**kwargs):
    frame = reply(**({"sequence": 19, "message": 101, "request": 201} | kwargs))
    return finish_mode2(bytearray(frame), NODE.session_key), NODE.address


def test_one_start_and_identical_release_retries_stay_on_same_route(prepared):
    route, sock = prepared
    assert sock.sent == []
    route.send_start()
    with pytest.raises(RuntimeError):
        route.send_start()
    route.send_release()
    route.send_release()
    assert sock.sent[1] == sock.sent[2]
    assert all(peer == NODE.address for _, peer in sock.sent)
    route.close()
    route.close()
    assert sock.closed == 1
    with pytest.raises(RuntimeError):
        route.send_release()


def test_start_receipts_do_not_confirm_release(prepared):
    route, sock = prepared
    route.send_release()
    sock.incoming.extend((packet(ack=True, sequence=18), packet(kind=0xBA, message=100)))
    assert not route.confirm_release(deadline=101)


def test_explicit_error_overrides_both_receipts_and_success(prepared):
    route, sock = prepared
    route.send_release()
    sock.incoming.extend((packet(ack=True), packet(kind=0xBA), packet(), packet(value={"type": 2, "err": 7})))
    assert not route.confirm_release(deadline=101)
    sock.incoming.append(packet())
    assert not route.confirm_release(deadline=101)


def test_receipts_confirm_delivery_not_physical_stop(prepared):
    route, sock = prepared
    assert not route.confirm_release(deadline=101)
    route.send_release()
    sock.incoming.extend((packet(ack=True), packet(kind=0xBA)))
    assert route.confirm_release(deadline=101)


def test_foreign_peer_oversize_and_junk_are_bounded(prepared):
    route, sock = prepared
    route.send_release()
    wire, _ = packet()
    sock.incoming.extend([(wire, ("192.0.2.99", 1)), (bytes(4097), NODE.address)] + [(b"junk", NODE.address)] * 100)
    assert not route.confirm_release(deadline=101)
    assert sock.received == 64
    assert not route.confirm_release(deadline=100)
    assert sock.received == 64


def test_release_only_prevents_later_start(prepared):
    route, _ = prepared
    route.send_release()
    with pytest.raises(RuntimeError):
        route.send_start()
