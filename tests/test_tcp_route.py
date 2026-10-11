import ipaddress
import struct

import pytest

from backend.app.drivers.yoosee.p2p import tcp_route as route
from backend.app.drivers.yoosee.p2p.av_sample import AvVideoSample
from backend.app.drivers.yoosee.p2p.contracts import CertifiedNode, OnlineDevice
from backend.app.drivers.yoosee.p2p.crypto import gute_mode1_xor_checksum
from backend.app.drivers.yoosee.p2p.mtp_tcp_receive import TcpReception
from backend.app.drivers.yoosee.p2p.wire import finish_mode2
from tests.test_av_route import ENROLLMENT
from tests.test_vendor_mtp_relays import packet
from tests.test_vendor_resource_service_protocol import _fragment


def advertisement(address="8.8.8.8", link=31):
    frame = bytearray(packet(v4=1, v6=0))
    struct.pack_into("<I", frame, 28, link)
    frame[0x86:0x8A] = ipaddress.ip_address(address).packed
    struct.pack_into("<I", frame, 16, gute_mode1_xor_checksum(frame))
    return bytes(frame)


def test_selection_requires_correlated_global_ipv4_table():
    assert route.select_relay(advertisement(), 17, 31) == ("8.8.8.8", 0x584D)
    assert route.select_relay(advertisement("127.0.0.1"), 17, 31) is None
    assert route.select_relay(advertisement("192.168.1.1"), 17, 31) is None
    assert route.select_relay(advertisement(), 18, 31) is None
    assert route.select_relay(advertisement(), 17, 32) is None
    assert route.select_relay(bytes(8), 17, 31) is None


def test_identity_mismatch_never_allocates_socket(monkeypatch):
    def forbidden(*args):
        pytest.fail("unreviewed target must not allocate a socket")
    monkeypatch.setattr(route.socket, "socket", forbidden)
    with pytest.raises(ValueError, match="enrollment"):
        route.probe_tcp_route(ENROLLMENT, camera_id="wrong", device_id="123")


@pytest.mark.parametrize("fault", [None, "connect", "pair", "receive", "release", "a4", "close", "advert"])
@pytest.mark.parametrize("fragmented", [False, True])
def test_one_socket_attempt_and_cleanup_order(monkeypatch, fault, fragmented):
    node = CertifiedNode(("192.0.2.5", 19800), 17, bytes(range(32)), 40)
    target = OnlineDevice(123, 1, False, 1, bytes(16))
    events, owners, sockets = [], [], []
    sample = AvVideoSample()
    sample.data.extend(b"private")

    class Socket:
        def __init__(self, *args):
            self.closed = False
            sockets.append(self)
        def bind(self, addr): pass
        def getsockname(self): return ("192.0.2.6", 12345)
        def settimeout(self, value): pass
        def connect(self, endpoint):
            events.append("connect")
            if fault == "connect": raise OSError("failed")
        def sendall(self, wire):
            events.append("pair")
            if fault == "pair": raise OSError("partial")
        def sendto(self, wire, peer):
            if wire[1] == 0xA4:
                events.append("a4")
                if fault == "a4": raise OSError("ambiguous A4")
            return len(wire)
        def close(self):
            self.closed = True
            if fault == "close": raise OSError("close failure")

    def capture(sock, peer, owner, **kwargs):
        owners.append(owner)
        events.append("receive")
        if fault == "receive": raise ValueError("bad media")
        return TcpReception(True, True, 3, 0, 1, 1000, 100)

    def release(*args, **kwargs):
        events.append("release")
        assert not any(sock.closed for sock in sockets)
        assert kwargs["require_correlated_ack"]
        if fault == "release": raise OSError("no receipt")
        return True

    monkeypatch.setattr(route.socket, "socket", Socket)
    monkeypatch.setattr(route, "open_camera_session", lambda *args: (node, target, 40))
    monkeypatch.setattr(route, "local_route_ip", lambda _: "192.0.2.6")
    monkeypatch.setattr(route.secrets, "randbelow", lambda _: 30)
    wire = finish_mode2(bytearray(advertisement(link=32 if fault == "advert" else 31)), node.session_key)
    packets = [_fragment(1, 2, wire, 80), _fragment(0, 2, wire, 80)] if fragmented else [wire]
    monkeypatch.setattr(route, "receive_datagrams", lambda *args: iter((part, node.address) for part in packets))
    monkeypatch.setattr(route, "receive_paired_av", capture)
    monkeypatch.setattr(route, "close_device_route", release)
    if fault not in (None, "release"):
        with pytest.raises((OSError, ValueError, route.P2PProbeError)):
            route.probe_tcp_route(ENROLLMENT, camera_id="cam_test", device_id="123", sample=sample)
        assert sample.closed and not sample.data
    else:
        result = route.probe_tcp_route(ENROLLMENT, camera_id="cam_test", device_id="123", sample=sample)
        assert result.broker_release is (fault is None)
        sample.close()
    assert events.count("release") == 1
    assert all(sock.closed for sock in sockets)
    assert all(owner.closed for owner in owners)
