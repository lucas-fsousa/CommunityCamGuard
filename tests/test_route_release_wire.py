"""Exercise broker-release correlation through the real mode-2 codec."""

import struct

import pytest

from backend.app.drivers.yoosee.p2p import rendezvous_session
from backend.app.drivers.yoosee.p2p.contracts import CertifiedNode, OnlineDevice
from backend.app.drivers.yoosee.p2p.crypto import gute_mode2_decrypt
from backend.app.drivers.yoosee.p2p.wire import finish_mode1, finish_mode2, new_header


@pytest.mark.parametrize("fault", [None, "peer", "session", "sequence", "type",
                                  "mode", "key", "checksum", "short",
                                  "need_certify", "signature_error", "unknown_result"])
def test_strict_release_with_encrypted_wire(monkeypatch, fault):
    node = CertifiedNode(("192.0.2.10", 19800), 9, bytes(range(32)), 17)
    device = OnlineDevice(7000000002, 1, False, 1, bytes(16))
    frame = new_header(0xAA if fault == "type" else 0xB9, 32,
                       10 if fault == "session" else 9,
                       19 if fault == "sequence" else 18,
                       (1 << 20) | ((1 if fault == "mode" else 2) << 16))
    frame[0] = 0x7E
    struct.pack_into("<I", frame, 24, 4)
    struct.pack_into("<H", frame, 26,
                     {"need_certify": 1, "signature_error": 4,
                      "unknown_result": 65535}.get(fault, 0))
    wire = (finish_mode1(frame) if fault == "mode" else
            finish_mode2(frame, bytes(32) if fault == "key" else node.session_key))
    if fault == "checksum":
        wire = wire[:16] + bytes((wire[16] ^ 1,)) + wire[17:]
    if fault == "short":
        wire = wire[:23]
    peer = (node.address[0], node.address[1] + 1) if fault == "peer" else node.address
    sent = []

    class Socket:
        def sendto(self, data, address):
            sent.append((data, address))
            return len(data)

    monkeypatch.setattr(rendezvous_session, "receive_datagrams",
                        lambda *_args: iter([(wire, peer)]))
    result = rendezvous_session.close_device_route(
        Socket(), node, 123, device, 42, 18, 0.1, require_correlated_ack=True)
    assert result is (fault is None)
    assert len(sent) == 1  # No automatic replay on missing/invalid receipt.
    request, address = sent[0]
    assert address == node.address
    plain = gute_mode2_decrypt(request, node.session_key)
    assert plain[1] == 0xB9
    assert (struct.unpack_from("<I", plain, 20)[0] >> 18) & 3 == 1


@pytest.mark.parametrize("result", [1, 4, 65535])
def test_legacy_release_mode_also_rejects_negative_result(monkeypatch, result):
    node = CertifiedNode(("192.0.2.10", 19800), 9, bytes(range(32)), 17)
    device = OnlineDevice(7000000002, 1, False, 1, bytes(16))
    frame = new_header(0xB9, 32, 9, 18, (1 << 20) | (2 << 16))
    frame[0] = 0x7E
    struct.pack_into("<HH", frame, 24, 4, result)
    wire = finish_mode2(frame, node.session_key)
    sent = []

    class Socket:
        def sendto(self, data, address):
            sent.append((data, address))
            return len(data)

    monkeypatch.setattr(rendezvous_session, "receive_datagrams",
                        lambda *_args: iter([(wire, node.address)]))
    assert not rendezvous_session.close_device_route(Socket(), node, 123, device, 42, 18, 0.1)
    assert len(sent) == 1
