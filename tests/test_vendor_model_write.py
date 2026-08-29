from __future__ import annotations

import struct

import pytest

from backend.app.drivers.yoosee.p2p import model_write_session
from backend.app.drivers.yoosee.p2p.contracts import CertifiedNode, OnlineDevice
from backend.app.drivers.yoosee.p2p.crypto import gute_mode2_decrypt
from backend.app.drivers.yoosee.p2p.model_write_protocol import (
    build_model_write,
    parse_model_write_response,
)


def test_scalar_model_write_encodes_json_string_without_allowing_a_payload_object():
    node = CertifiedNode(("192.0.2.10", 19800), 9, bytes(range(32)), 17)
    plain = gute_mode2_decrypt(
        build_model_write(
            node,
            7000000002,
            "ProWritable.resFile.setVal.resId",
            "opaque-resource",
            18,
            19,
        ),
        node.session_key,
    )

    path_length = plain[0x27]
    value_length = struct.unpack_from("<H", plain, 0x28)[0]
    cursor = 0x32 + path_length + 1
    assert plain[cursor : cursor + value_length] == b'"opaque-resource"'

    with pytest.raises(ValueError, match="integer or string scalar"):
        build_model_write(  # type: ignore[arg-type]
            node, 7000000002, "ProWritable.resFile.setVal", {"resId": "unsafe"}, 18, 19
        )
    with pytest.raises(ValueError, match="writable setVal leaf"):
        build_model_write(node, 7000000002, "Action.expelCtrl.stVal", 2, 18, 19)


def test_model_write_response_requires_matching_d3_message_id():
    response = bytearray(0x36)
    response[:2] = b"\x7e\xd3"
    struct.pack_into("<I", response, 0x30, 19)
    struct.pack_into("<H", response, 0x34, 7)

    assert parse_model_write_response(response, 19) == 7
    assert parse_model_write_response(response, 20) is None


def test_model_write_session_collects_transport_ack_and_application_result(monkeypatch):
    node = CertifiedNode(("192.0.2.10", 19800), 9, bytes(range(32)), 17)
    device = OnlineDevice(7000000002, 1, False, 1, bytes(16))
    sent = []
    acknowledged = []

    class FakeSocket:
        def sendto(self, wire, peer):
            sent.append((wire, peer))

    transport_ack = bytearray(0x20)
    transport_ack[:2] = b"\x7e\xd2"
    struct.pack_into("<I", transport_ack, 0x14, 1 << 20)
    application = bytearray(0x36)
    application[:2] = b"\x7e\xd3"
    struct.pack_into("<I", application, 0x30, 19)
    struct.pack_into("<H", application, 0x34, 0)

    monkeypatch.setattr(model_write_session.secrets, "randbits", lambda _bits: 19)
    monkeypatch.setattr(
        model_write_session,
        "receive_datagrams",
        lambda *_args: [(bytes(transport_ack), node.address), (bytes(application), node.address)],
    )
    monkeypatch.setattr(model_write_session, "decrypt_node_frame", lambda wire, _node: wire)
    monkeypatch.setattr(
        model_write_session,
        "acknowledge_reliable_node_frame",
        lambda _sock, _node, frame: acknowledged.append(frame[1]),
    )

    result = model_write_session.exchange_model_write(
        FakeSocket(),  # type: ignore[arg-type]
        node,
        device,
        "ProWritable.videoParm.setVal.nightViewMode",
        2,
        18,
        1.0,
    )

    assert len(sent) == 1
    assert sent[0][1] == node.address
    assert acknowledged == [0xD3]
    assert result.transport_acknowledged is True
    assert result.error_code == 0
