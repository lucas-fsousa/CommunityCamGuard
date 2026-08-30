from __future__ import annotations

import struct

from backend.app.drivers.yoosee.p2p import rendezvous_session
from backend.app.drivers.yoosee.p2p.contracts import CertifiedNode, OnlineDevice
from backend.app.drivers.yoosee.p2p.crypto import gute_mode2_decrypt
from backend.app.drivers.yoosee.p2p.rendezvous_protocol import build_route_hangup


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
    assert 0 < result.route_link_id <= 0xFFFFFF
    assert result.next_sequence == 18
    assert sent == [
        (b"calling", node.address),
        (b"online", peer),
        (b"ack", peer),
    ]


def test_route_hangup_matches_the_native_p2p_inner_layout():
    node = CertifiedNode(("192.0.2.10", 19800), 9, bytes(range(32)), 17)

    plain = gute_mode2_decrypt(
        build_route_hangup(node, 123, 7000000002, 0x123456, 18, 19),
        node.session_key,
    )

    assert plain[:2] == b"\x7e\xb9"
    assert len(plain) == 0x4C
    assert struct.unpack_from("<Q", plain, 4)[0] == node.session_id
    assert struct.unpack_from("<I", plain, 0x0C)[0] == 18
    assert (struct.unpack_from("<I", plain, 0x14)[0] >> 16) & 3 == 2
    assert (struct.unpack_from("<I", plain, 0x14)[0] >> 18) & 3 == 3
    assert struct.unpack_from("<I", plain, 0x18)[0] == 1
    assert struct.unpack_from("<Q", plain, 0x1C)[0] == 7000000002
    assert struct.unpack_from("<Q", plain, 0x24)[0] == 123
    assert struct.unpack_from("<I", plain, 0x2C)[0] == 19
    assert plain[0x34:0x38] == bytes(4)
    assert struct.unpack_from("<II", plain, 0x38) == (0x123456, 0x123456)
    assert struct.unpack_from("<I", plain, 0x40)[0] == 0x4E22
    assert plain[0x44:0x4C] == bytes(8)


def test_route_close_sends_once_and_accepts_transport_ack(monkeypatch):
    node = CertifiedNode(("192.0.2.10", 19800), 9, bytes(32), 17)
    device = OnlineDevice(7000000002, 1, False, 1, bytes(16))
    sent = []
    ack = bytearray(32)
    ack[1] = 0xB9
    struct.pack_into("<I", ack, 0x14, 1 << 20)

    class FakeSocket:
        def sendto(self, payload, address):
            sent.append((payload, address))

    monkeypatch.setattr(rendezvous_session, "build_route_hangup", lambda *_args: b"hangup")
    monkeypatch.setattr(
        rendezvous_session,
        "receive_datagrams",
        lambda *_args: iter(((b"ack", node.address),)),
    )
    monkeypatch.setattr(
        rendezvous_session,
        "decrypt_node_frame",
        lambda *_args: bytes(ack),
    )

    assert rendezvous_session.close_device_route(
        FakeSocket(),  # type: ignore[arg-type]
        node,
        123,
        device,
        0x123456,
        18,
        0.1,
    )
    assert sent == [(b"hangup", node.address)]
