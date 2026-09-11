"""Strict catalogue replies correlate the inner frame, not outer fragment group IDs."""
import struct

import pytest

from backend.app.drivers.yoosee.p2p import resource_service_session as session
from backend.app.drivers.yoosee.p2p.resource_service_protocol import (
    decode_fragment_packet,
    encode_fragment_header,
)
from tests.test_vendor_resource_service_session import NODE, QUERY, FakeSocket, _fragment, _plain


def plain(*, identity=NODE.session_id, correlation=8, sequence=8, mode=2, ack=False):
    frame = bytearray(_plain(0xC0 if ack else 0xC1, b'{"code":0}',
                             flags=(mode << 16) | (int(ack) << 20)))
    struct.pack_into("<Q", frame, 4, identity)
    struct.pack_into("<I", frame, 12, sequence)
    struct.pack_into("<I", frame, 16, correlation)
    return bytes(frame)


def run(monkeypatch, frames):
    monkeypatch.setattr(session, "build_alarm_voice_catalog_request", lambda *_: b"request")
    monkeypatch.setattr(session, "receive_datagrams", lambda *_: iter(frames))
    monkeypatch.setattr(session, "decrypt_node_frame", lambda wire, _node: wire)
    monkeypatch.setattr(session, "acknowledge_reliable_node_frame", lambda *_: True)
    return session.exchange_alarm_voice_catalog(
        FakeSocket(), NODE, QUERY, 8, .1, retries=1, require_correlated_response=True,
    )


@pytest.mark.parametrize("changes", [
    {"identity": 17}, {"correlation": 9}, {"mode": 0},
])
def test_foreign_stale_or_plaintext_replies_are_not_accepted(monkeypatch, changes):
    result = run(monkeypatch, [(plain(**changes), NODE.address)])
    assert result.status_code is None and result.payload is None


def test_matching_reply_is_used_after_stale_reply(monkeypatch):
    result = run(monkeypatch, [(plain(correlation=9), NODE.address), (plain(), NODE.address)])
    assert result.status_code == 0 and result.payload == b'{"code":0}'


@pytest.mark.parametrize("sequence,expected", [(8, True), (9, False)])
def test_ack_sequence_is_checked_separately_from_application_success(monkeypatch, sequence, expected):
    result = run(monkeypatch, [(plain(ack=True, sequence=sequence), NODE.address)])
    assert result.transport_acknowledged is expected
    assert result.status_code is None


def test_wrong_peer_is_not_accepted(monkeypatch):
    assert run(monkeypatch, [(plain(), ("192.0.2.20", 19800))]).payload is None


def test_transport_fragment_identity_is_not_mistaken_for_access_node_session(monkeypatch):
    response = plain()
    count = (len(response) + 23) // 24
    wires = []
    for i in reversed(range(count)):
        packet = decode_fragment_packet(_fragment(response, i, count, 24))
        frame = bytearray(packet.decoded_header + packet.payload)
        struct.pack_into("<Q", frame, 4, 123)
        wires.append((encode_fragment_header(frame, mask=0x4400+i), NODE.address))
    result = run(monkeypatch, wires)
    assert result.status_code == 0 and result.payload == b'{"code":0}'
    assert result.fragments_received == count
