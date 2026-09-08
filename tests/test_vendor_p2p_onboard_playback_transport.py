from __future__ import annotations

import struct

import pytest

from backend.app.drivers.yoosee.p2p import onboard_playback_transport
from backend.app.drivers.yoosee.p2p.contracts import CertifiedNode, P2PProbeError


class _Socket:
    def __init__(self) -> None:
        self.sent: list[tuple[bytes, tuple[str, int]]] = []

    def sendto(self, payload: bytes, peer: tuple[str, int]) -> None:
        self.sent.append((payload, peer))


def test_live_playback_transport_fails_closed_until_physically_certified():
    with pytest.raises(P2PProbeError, match="not runtime-certified"):
        onboard_playback_transport.require_runtime_playback_read_certified()


def _frame(kind: int, *, flags: int = 0, message_id: int = 0) -> bytes:
    frame = bytearray(0x34)
    frame[:2] = bytes((0x7E, kind))
    struct.pack_into("<I", frame, 0x14, flags)
    struct.pack_into("<I", frame, 0x2C, message_id)
    return bytes(frame)


def test_exchange_collects_correlated_response_and_sends_both_receipts(monkeypatch):
    node = CertifiedNode(("192.0.2.10", 19800), 9, bytes(range(32)), 17)
    sock = _Socket()
    transport_ack = _frame(0xB9, flags=1 << 20)
    application_ack = _frame(0xBA, message_id=44)
    response = _frame(0xB9, message_id=44)
    reliable_acks: list[bytes] = []

    monkeypatch.setattr(
        onboard_playback_transport,
        "receive_datagrams",
        lambda *_args: iter(
            ((transport_ack, node.address), (application_ack, node.address), (response, node.address))
        ),
    )
    monkeypatch.setattr(onboard_playback_transport, "decrypt_node_frame", lambda wire, _node: wire)
    monkeypatch.setattr(
        onboard_playback_transport,
        "acknowledge_reliable_node_frame",
        lambda _sock, _node, frame: reliable_acks.append(frame) or True,
    )
    monkeypatch.setattr(
        onboard_playback_transport,
        "build_onboard_playback_receipt",
        lambda *_args: b"application-receipt",
    )

    result = onboard_playback_transport.exchange_built_in_read(
        sock,  # type: ignore[arg-type]
        node,
        b"request",
        message_id=44,
        sequence=17,
        timeout=0.5,
        parse_response=lambda frame: "page" if frame is response else None,
        response_set_complete=lambda pages: pages == ("page",),
    )

    assert result.transport_acknowledged is True
    assert result.application_acknowledged is True
    assert result.responses == ("page",)
    assert reliable_acks == [application_ack, response]
    assert sock.sent == [
        (b"request", node.address),
        (b"application-receipt", node.address),
    ]


def test_exchange_is_bounded_when_no_response_arrives(monkeypatch):
    node = CertifiedNode(("192.0.2.10", 19800), 9, bytes(range(32)), 17)
    sock = _Socket()
    monkeypatch.setattr(onboard_playback_transport, "receive_datagrams", lambda *_args: iter(()))

    result = onboard_playback_transport.exchange_built_in_read(
        sock,  # type: ignore[arg-type]
        node,
        b"request",
        message_id=44,
        sequence=17,
        timeout=0.01,
        parse_response=lambda _frame: None,
        response_set_complete=lambda _responses: False,
        retries=2,
    )

    assert result.responses == ()
    assert sock.sent == [(b"request", node.address), (b"request", node.address)]
