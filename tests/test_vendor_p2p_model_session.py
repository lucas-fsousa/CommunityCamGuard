from __future__ import annotations

import struct

from backend.app.drivers.yoosee.p2p import model_session
from backend.app.drivers.yoosee.p2p.contracts import CertifiedNode, OnlineDevice


def test_model_read_correlates_transport_ack_and_nested_report(monkeypatch):
    node = CertifiedNode(("192.0.2.10", 19800), 9, bytes(32), 17)
    device = OnlineDevice(7000000002, 1, False, 1, bytes(16))
    path = "ProWritable.videoParm"
    ack = bytearray(32)
    ack[1] = 0xB7
    struct.pack_into("<I", ack, 0x14, 1 << 20)
    report = bytearray(32)
    report[1] = 0xAA
    sent: list[tuple[bytes, tuple[str, int]]] = []
    acknowledged: list[bytes] = []

    class FakeSocket:
        def sendto(self, payload, address):
            sent.append((payload, address))

    monkeypatch.setattr(model_session.secrets, "randbits", lambda _bits: 19)
    monkeypatch.setattr(model_session, "build_model_read", lambda *_args: b"request")
    monkeypatch.setattr(
        model_session,
        "receive_datagrams",
        lambda *_args: iter(((b"ack", node.address), (b"report", node.address))),
    )
    monkeypatch.setattr(
        model_session,
        "decrypt_node_frame",
        lambda wire, _node: bytes(ack if wire == b"ack" else report),
    )
    monkeypatch.setattr(
        model_session,
        "parse_model_report",
        lambda frame: (
            (device.device_id, path + ".setVal", {"multiFlip": 1}) if frame[1] == 0xAA else None
        ),
    )
    monkeypatch.setattr(
        model_session,
        "acknowledge_reliable_node_frame",
        lambda _sock, _node, frame: acknowledged.append(frame),
    )

    result = model_session.exchange_model_read(
        FakeSocket(),  # type: ignore[arg-type]
        node,
        device,
        path,
        18,
        0.1,
        retries=1,
    )

    assert result.transport_acknowledged is True
    assert result.error_code == 0
    assert result.value == {"multiFlip": 1}
    assert sent == [(b"request", node.address)]
    assert acknowledged == [bytes(report)]
