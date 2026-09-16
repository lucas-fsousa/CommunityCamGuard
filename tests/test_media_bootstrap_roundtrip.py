"""Synthetic UDP only: bootstrap evidence must belong to a sent measurement."""

import struct

import pytest

from backend.app.drivers.yoosee.p2p import media_session
from backend.app.drivers.yoosee.p2p.media_protocol import build_media_meter_ack, build_mtp_frame
from tests.test_vendor_media_session import _route


@pytest.mark.parametrize("fault", [None, "peer", "node", "link", "source", "destination",
                                  "kind", "channel", "length", "role", "sequence", "timestamp",
                                  "timestamp_high", "call", "checksum"])
def test_only_correlated_meter_ack_confirms_roundtrip(monkeypatch, fault):
    node, device, attempt, calling = _route()
    sent = []

    class Socket:
        def getsockname(self):
            return "0.0.0.0", 45678

        def sendto(self, wire, peer):
            sent.append((wire, peer))

    def receive(*args):
        request = next(wire for wire, _ in reversed(sent) if wire[:2] == b"\xc0\x90")
        inner = bytearray(build_media_meter_ack(request)[6:])
        mutations = {"link": (4, "I"), "source": (12, "Q"), "destination": (20, "Q"),
                     "kind": (1, "B"), "channel": (48, "I"), "length": (52, "I"),
                     "role": (65, "B"), "sequence": (28, "I"), "timestamp": (32, "I"),
                     "timestamp_high": (36, "I")}
        if fault in mutations:
            offset, fmt = mutations[fault]
            struct.pack_into("<" + fmt, inner, offset, 99)
        if fault == "call":
            inner.extend(struct.pack("<I", attempt.call_id ^ 1))
            struct.pack_into("<I", inner, 52, len(inner))
        wire = build_mtp_frame(0x90, inner)
        if fault == "checksum":
            wire = wire[:4] + bytes((wire[4] ^ 1,)) + wire[5:]
        peer = ("192.0.2.99", 1) if fault == "peer" else calling.peer_endpoint
        if fault == "node":
            peer = node.address
        yield wire, peer

    monkeypatch.setattr(media_session, "local_route_ip", lambda peer: "192.0.2.20")
    monkeypatch.setattr(media_session, "build_direct_calling_request", lambda *a, **k: b"direct")
    monkeypatch.setattr(media_session, "decrypt_node_frame", lambda *a: None)
    monkeypatch.setattr(media_session, "receive_datagrams", receive)
    result = media_session.open_media_channel(Socket(), node, 123, device, calling, 0.1,
                                               require_roundtrip=True)
    assert result.meter_roundtrip_confirmed is (fault is None)
    assert not result.direct_acknowledged
    expected = {None: ("reply",), "peer": (), "node": (), "link": (), "source": (),
                "destination": (), "checksum": (), "kind": ("unknown_kind",),
                "channel": ("reply", "wrong_channel"),
                "length": ("reply", "wrong_record_length"), "role": ("reply", "wrong_role"),
                "call": ("reply", "wrong_call"),
                "sequence": ("reply", "unmatched_timestamp", "unsent_sequence"),
                "timestamp": ("reply", "unmatched_timestamp"),
                "timestamp_high": ("reply", "unmatched_timestamp")}
    assert result.meter_observations == expected[fault]
    assert len(sent) == (2 if fault is None else 4)


def test_request_only_is_not_roundtrip_proof(monkeypatch):
    from backend.app.drivers.yoosee.p2p.media_protocol import build_media_meter_request

    node, device, attempt, calling = _route()
    sent = []

    class Socket:
        def getsockname(self):
            return "0.0.0.0", 45678

        def sendto(self, wire, peer):
            sent.append(wire)

    request = build_media_meter_request(device.device_id, 123, attempt.link_id, attempt.call_id)
    monkeypatch.setattr(media_session, "local_route_ip", lambda peer: "192.0.2.20")
    monkeypatch.setattr(media_session, "build_direct_calling_request", lambda *a, **k: b"direct")
    monkeypatch.setattr(media_session, "receive_datagrams",
                        lambda *a: iter(((request, calling.peer_endpoint),)))
    result = media_session.open_media_channel(Socket(), node, 123, device, calling, 0.1,
                                               require_roundtrip=True)
    assert result.meter_acknowledged and not result.meter_roundtrip_confirmed
    assert result.meter_observations == ("request",)
    assert len(sent) == 6  # Two bounded attempts, including replies to peer requests.
