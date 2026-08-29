from __future__ import annotations

import struct

from backend.app.drivers.yoosee.p2p import rendezvous_session
from backend.app.drivers.yoosee.p2p.contracts import CertifiedNode, OnlineDevice


def test_direct_rendezvous_counts_and_acknowledges_camera_datagram(monkeypatch):
    node = CertifiedNode(("192.0.2.10", 19800), 9, bytes(32), 17)
    device = OnlineDevice(7000000002, 1, False, 1, bytes(16))
    peer = ("198.51.100.9", 32100)
    direct = bytearray(52)
    struct.pack_into("<I", direct, 0x24, 7)
    wire = b"\x7f\xca" + bytes(50)
    sent: list[tuple[bytes, tuple[str, int]]] = []

    class FakeSocket:
        def getsockname(self):
            return "0.0.0.0", 45678

        def sendto(self, payload, address):
            sent.append((payload, address))

    monkeypatch.setattr(rendezvous_session.secrets, "randbelow", lambda _limit: 6)
    monkeypatch.setattr(rendezvous_session.secrets, "randbits", lambda _bits: 8)
    monkeypatch.setattr(rendezvous_session.secrets, "token_bytes", lambda length: b"x" * length)
    monkeypatch.setattr(rendezvous_session, "local_route_ip", lambda _peer: "192.0.2.20")
    monkeypatch.setattr(rendezvous_session, "build_calling_request", lambda *_args: b"calling")
    monkeypatch.setattr(rendezvous_session, "build_nat_online", lambda *_args: b"online")
    monkeypatch.setattr(rendezvous_session, "build_nat_online_ack", lambda *_args: b"ack")
    monkeypatch.setattr(rendezvous_session, "gute_mode0_decrypt", lambda _wire: bytes(direct))
    monkeypatch.setattr(
        rendezvous_session,
        "receive_datagrams",
        lambda *_args: iter(((wire, peer),)),
    )

    result = rendezvous_session.call_device(
        FakeSocket(),  # type: ignore[arg-type]
        node,
        123,
        device,
        0.1,
        retries=1,
    )

    assert result.direct_datagrams == 1
    assert result.direct_handshake is True
    assert result.next_sequence == 18
    assert sent == [
        (b"calling", node.address),
        (b"online", peer),
        (b"ack", peer),
    ]
