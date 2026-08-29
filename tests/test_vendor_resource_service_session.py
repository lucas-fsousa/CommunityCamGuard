from __future__ import annotations

import struct

from backend.app.drivers.yoosee.p2p import resource_service_session
from backend.app.drivers.yoosee.p2p.alarm_voice import build_alarm_voice_query
from backend.app.drivers.yoosee.p2p.contracts import CertifiedNode
from backend.app.drivers.yoosee.p2p.resource_service_protocol import encode_fragment_header

NODE = CertifiedNode(("192.0.2.10", 19800), 0x1020304050607080, bytes(32), 7)
QUERY = build_alarm_voice_query(system=True, language="pt-BR", access_id=123)


def _plain(subtype: int, payload: bytes = b"", *, flags: int = 0) -> bytes:
    frame = bytearray(0x20 + len(payload))
    frame[:2] = bytes((0x7E, subtype))
    struct.pack_into("<H", frame, 2, len(frame))
    struct.pack_into("<I", frame, 0x10, 11)
    struct.pack_into("<I", frame, 0x14, flags)
    if payload:
        frame[0x18] = 1
        struct.pack_into("<H", frame, 0x1E, len(payload))
        frame[0x20:] = payload
    return bytes(frame)


def _fragment(original: bytes, index: int, total: int, chunk_size: int) -> bytes:
    payload = original[index * chunk_size : (index + 1) * chunk_size]
    frame = bytearray(0x18 + len(payload))
    frame[:2] = b"\x70\x01"
    struct.pack_into("<H", frame, 2, len(frame))
    struct.pack_into("<Q", frame, 4, NODE.session_id)
    struct.pack_into("<I", frame, 0x0C, 19)
    struct.pack_into("<H", frame, 0x10, len(original))
    frame[0x12] = total
    frame[0x13] = index
    frame[0x18:] = payload
    return encode_fragment_header(frame, mask=0x4400 + index)


class FakeSocket:
    def __init__(self) -> None:
        self.sent: list[tuple[bytes, tuple[str, int]]] = []

    def sendto(self, payload: bytes, address: tuple[str, int]) -> None:
        self.sent.append((payload, address))


def test_catalog_session_correlates_ack_and_uncompressed_response(monkeypatch):
    sock = FakeSocket()
    transport_ack = _plain(0xC0, flags=1 << 20)
    response = _plain(0xC1, b'{"code":0}')
    acknowledged: list[bytes] = []
    monkeypatch.setattr(resource_service_session, "build_alarm_voice_catalog_request", lambda *_: b"request")
    monkeypatch.setattr(
        resource_service_session,
        "receive_datagrams",
        lambda *_: iter(((b"ack", NODE.address), (b"response", NODE.address))),
    )
    monkeypatch.setattr(
        resource_service_session,
        "decrypt_node_frame",
        lambda wire, _node: transport_ack if wire == b"ack" else response,
    )
    monkeypatch.setattr(
        resource_service_session,
        "acknowledge_reliable_node_frame",
        lambda _sock, _node, frame: acknowledged.append(frame),
    )

    result = resource_service_session.exchange_alarm_voice_catalog(
        sock,  # type: ignore[arg-type]
        NODE,
        QUERY,
        8,
        0.1,
        retries=1,
    )

    assert result.transport_acknowledged is True
    assert result.status_code == 0
    assert result.payload == b'{"code":0}'
    assert result.compression_required is False
    assert result.fragments_received == 0
    assert sock.sent == [(b"request", NODE.address)]
    assert acknowledged == [response]


def test_catalog_session_reassembles_and_acknowledges_every_fragment(monkeypatch):
    sock = FakeSocket()
    response = _plain(0xC1, b'{"code":0}')
    opaque_wire = b"encrypted:" + response
    chunk_size = 24
    total = (len(opaque_wire) + chunk_size - 1) // chunk_size
    wires = tuple(
        (_fragment(opaque_wire, index, total, chunk_size), NODE.address)
        for index in reversed(range(total))
    )
    monkeypatch.setattr(resource_service_session, "build_alarm_voice_catalog_request", lambda *_: b"request")
    monkeypatch.setattr(resource_service_session, "receive_datagrams", lambda *_: iter(wires))
    monkeypatch.setattr(
        resource_service_session,
        "decrypt_node_frame",
        lambda wire, _node: response if wire == opaque_wire else None,
    )
    monkeypatch.setattr(resource_service_session, "acknowledge_reliable_node_frame", lambda *_: True)

    result = resource_service_session.exchange_alarm_voice_catalog(
        sock,  # type: ignore[arg-type]
        NODE,
        QUERY,
        8,
        0.1,
        retries=1,
    )

    assert result.status_code == 0
    assert result.fragments_received == total
    assert sock.sent[0] == (b"request", NODE.address)
    assert all(payload[:2] == b"\x70\x02" for payload, _address in sock.sent[1:])


def test_catalog_session_requires_explicit_compatible_decompressor(monkeypatch):
    sock = FakeSocket()
    compressed_payload = b"not-interpreted"
    decoded = _plain(0xC1, b'{"code":0}')[0x18:]
    expected = len(decoded)
    compressed = bytearray(0x18 + len(compressed_payload))
    compressed[:2] = b"\x7e\xc1"
    struct.pack_into("<H", compressed, 2, len(compressed))
    struct.pack_into("<I", compressed, 0x14, 1 | (expected << 1))
    compressed[0x18:] = compressed_payload
    monkeypatch.setattr(resource_service_session, "build_alarm_voice_catalog_request", lambda *_: b"request")
    monkeypatch.setattr(
        resource_service_session,
        "receive_datagrams",
        lambda *_: iter(((b"compressed", NODE.address),)),
    )
    monkeypatch.setattr(resource_service_session, "decrypt_node_frame", lambda *_: bytes(compressed))

    blocked = resource_service_session.exchange_alarm_voice_catalog(
        sock,  # type: ignore[arg-type]
        NODE,
        QUERY,
        8,
        0.1,
        retries=1,
        decompressor=None,
    )

    assert blocked.compression_required is True
    assert blocked.status_code is None
    assert blocked.payload is None

    completed = resource_service_session.exchange_alarm_voice_catalog(
        sock,  # type: ignore[arg-type]
        NODE,
        QUERY,
        8,
        0.1,
        retries=1,
        decompressor=lambda source, length: decoded
        if source == compressed_payload and length == expected
        else b"",
    )

    assert completed.compression_required is False
    assert completed.status_code == 0
    assert completed.payload == b'{"code":0}'


def test_catalog_session_uses_the_bounded_level2_decoder_by_default(monkeypatch):
    sock = FakeSocket()
    encoded = bytes.fromhex("791912000000800100000000000a007b22636f6465223a307d")
    expected = 18
    compressed = bytearray(0x18 + len(encoded))
    compressed[:2] = b"\x7e\xc1"
    struct.pack_into("<H", compressed, 2, len(compressed))
    struct.pack_into("<I", compressed, 0x14, 1 | (expected << 1))
    compressed[0x18:] = encoded
    monkeypatch.setattr(resource_service_session, "build_alarm_voice_catalog_request", lambda *_: b"request")
    monkeypatch.setattr(
        resource_service_session,
        "receive_datagrams",
        lambda *_: iter(((b"compressed", NODE.address),)),
    )
    monkeypatch.setattr(resource_service_session, "decrypt_node_frame", lambda *_: bytes(compressed))
    monkeypatch.setattr(resource_service_session, "acknowledge_reliable_node_frame", lambda *_: True)

    result = resource_service_session.exchange_alarm_voice_catalog(
        sock,  # type: ignore[arg-type]
        NODE,
        QUERY,
        8,
        0.1,
        retries=1,
    )

    assert result.compression_required is False
    assert result.status_code == 0
    assert result.payload == b'{"code":0}'


def test_catalog_session_validates_bounds_before_sending():
    sock = FakeSocket()

    for timeout, retries in ((0.0, 1), (0.1, 0)):
        try:
            resource_service_session.exchange_alarm_voice_catalog(
                sock,  # type: ignore[arg-type]
                NODE,
                QUERY,
                8,
                timeout,
                retries=retries,
            )
        except ValueError:
            pass
        else:
            raise AssertionError("invalid session bounds were accepted")
    assert sock.sent == []
