from __future__ import annotations

import struct

from backend.app.drivers.yoosee.p2p import media_session
from backend.app.drivers.yoosee.p2p.contracts import (
    CallingAttempt,
    CallingResult,
    CertifiedNode,
    OnlineDevice,
)
from backend.app.drivers.yoosee.p2p.crypto import gute_mode1_decrypt
from backend.app.drivers.yoosee.p2p.media_protocol import build_media_meter_request
from backend.app.drivers.yoosee.p2p.rendezvous_protocol import build_direct_calling_request


def _route() -> tuple[CertifiedNode, OnlineDevice, CallingAttempt, CallingResult]:
    node = CertifiedNode(("192.0.2.10", 19800), 9, bytes(32), 17)
    device = OnlineDevice(7_000_000_002, 1, False, 1, bytes(16))
    attempt = CallingAttempt(0x123456, 0x89ABCDEF, bytes.fromhex("aa17cd6974f58b1e"))
    calling = CallingResult(
        True,
        True,
        3,
        True,
        None,
        ("198.51.100.9", 32100),
        18,
        attempt.link_id,
        attempt,
    )
    return node, device, attempt, calling


def test_direct_calling_request_has_exact_private_media_fields() -> None:
    node, device, attempt, _calling = _route()
    plain = gute_mode1_decrypt(
        build_direct_calling_request(
            node,
            123,
            device,
            "192.0.2.20",
            45678,
            attempt,
            18,
        )
    )

    assert plain[:2] == b"\x7e\xa4"
    assert len(plain) == 177
    assert struct.unpack_from("<I", plain, 0x0C)[0] == 18
    assert (struct.unpack_from("<I", plain, 0x14)[0] >> 16) & 3 == 1
    assert struct.unpack_from("<H", plain, 0x18)[0] == 0x4483
    assert struct.unpack_from("<I", plain, 0x1C)[0] == attempt.link_id
    assert struct.unpack_from("<Q", plain, 0x20)[0] == 123
    assert struct.unpack_from("<Q", plain, 0x28)[0] == device.device_id
    assert plain[0x78:0x80] == attempt.cookie
    assert struct.unpack_from("<I", plain, 0x84)[0] == attempt.call_id
    assert plain[0xA7] == 0x12 and plain[0xB0] == 1


def test_media_channel_accepts_only_matching_peer_and_route(monkeypatch) -> None:
    node, device, attempt, calling = _route()
    peer = calling.peer_endpoint
    assert peer is not None
    direct_ack = bytearray(32)
    direct_ack[:2] = b"\x7e\xa4"
    struct.pack_into("<I", direct_ack, 0x0C, calling.next_sequence)
    struct.pack_into("<I", direct_ack, 0x18, 4)
    meter = build_media_meter_request(
        device.device_id,
        123,
        attempt.link_id,
        attempt.call_id,
    )
    sent: list[tuple[bytes, tuple[str, int]]] = []

    class FakeSocket:
        def getsockname(self):
            return "0.0.0.0", 45678

        def sendto(self, payload, address):
            sent.append((payload, address))

    monkeypatch.setattr(media_session, "local_route_ip", lambda _peer: "192.0.2.20")
    monkeypatch.setattr(media_session, "build_direct_calling_request", lambda *_args: b"direct")
    monkeypatch.setattr(media_session, "gute_mode1_decrypt", lambda _wire: bytes(direct_ack))
    monkeypatch.setattr(
        media_session,
        "receive_datagrams",
        lambda *_args: iter(
            (
                (b"ignored", (203, 0)),
                (bytes(direct_ack), peer),
                (meter, peer),
            )
        ),
    )

    result = media_session.open_media_channel(
        FakeSocket(),  # type: ignore[arg-type]
        node,
        123,
        device,
        calling,
        0.1,
    )

    assert result == media_session.MediaChannelResult(True, True, 2)
    assert [address for _payload, address in sent] == [peer, peer, peer]
    assert sent[0][0][:2] == b"\xc0\x90"
    assert sent[1][0] == b"direct"
    assert sent[2][0][:2] == b"\xc0\x90"


def test_media_channel_fails_closed_without_private_attempt() -> None:
    node, device, _attempt, calling = _route()
    missing = CallingResult(
        calling.node_acknowledged,
        calling.node_notified,
        calling.direct_datagrams,
        calling.direct_handshake,
        calling.error_code,
        calling.peer_endpoint,
        calling.next_sequence,
        calling.route_link_id,
    )
    assert media_session.open_media_channel(object(), node, 123, device, missing, 0.1) == (
        media_session.MediaChannelResult(False, False, 0)
    )
