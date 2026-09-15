import struct

import pytest

from backend.app.drivers.yoosee.p2p.av_receive import AvReceiver
from backend.app.drivers.yoosee.p2p.kcp_receive import ReceiveError
from backend.app.drivers.yoosee.p2p.stream_protocol import encrypt_media_tlv
from tests.test_captured_media import control, header
from tests.test_media_receive import PEER, packet
from tests.test_v1_receive import av

COOKIE = b"12345678"


def media(body):
    return encrypt_media_tlv(body, COOKIE)


def test_accept_fragment_reordering_header_split_and_continuous_media():
    receiver = AvReceiver(PEER, 42, 123, COOKIE)
    accept = control(123)
    assert not receiver.receive(packet(1, 0, accept[30:]), PEER).records
    assert not receiver.receive(packet(0, 1, accept[:30]), PEER).records
    assert receiver.phase == "initializing"
    assert not receiver.receive(packet(2, 0, media(header()[:13])), PEER).records
    result = receiver.receive(packet(3, 0, media(header()[13:] + av()[:15])), PEER)
    assert receiver.phase == "active" and result.records[0].encoding.video_width == 1920
    result = receiver.receive(packet(4, 0, media(av()[15:])), PEER)
    assert result.records[0].video == b"video"
    assert result.records[0].audio == (b"abc", b"defg")
    assert result.records[0].video_timestamp == 456
    assert receiver.buffered_bytes == 0
    assert not receiver.receive(packet(4, 0, media(av()[15:])), PEER).records
    assert "video" not in repr(result) and COOKIE.decode() not in repr(receiver)


@pytest.mark.parametrize("fault", ["call", "action", "flags", "length", "no_accept",
                                   "no_header", "cookie", "encoding_change", "media_flags"])
def test_protocol_failure_clears_both_layers(fault):
    receiver = AvReceiver(PEER, 42, 123, COOKIE)
    accept = bytearray(control(124 if fault == "call" else 123))
    if fault == "action":
        struct.pack_into("<I", accept, 8, 1)
    if fault == "flags":
        accept[1] = 2
    if fault == "length":
        accept.pop()
    with pytest.raises(ReceiveError, match="AV receive session failed"):
        if fault != "no_accept":
            receiver.receive(packet(0, 0, bytes(accept)), PEER)
        wire = media(av() if fault == "no_header" else header())
        if fault == "cookie":
            wire = encrypt_media_tlv(header(), b"87654321")
        if fault == "media_flags":
            wire = wire[:1] + b"\x80" + wire[2:]
        receiver.receive(packet(0 if fault == "no_accept" else 1, 0, wire), PEER)
        if fault == "encoding_change":
            changed = bytearray(header())
            struct.pack_into("<I", changed, 20, 640)
            receiver.receive(packet(2, 0, media(bytes(changed))), PEER)
    assert receiver.phase == "closed" and receiver.buffered_bytes == 0
    with pytest.raises(ReceiveError):
        receiver.receive(packet(0, 0, control(123)), PEER)


def test_accept_without_header_cannot_extend_initialization():
    now = [0.0]
    receiver = AvReceiver(PEER, 42, 123, COOKIE, clock=lambda: now[0])
    receiver.receive(packet(0, 0, control(123)), PEER)
    receiver.receive(packet(1, 0, media(header()[:13])), PEER)
    now[0] = 5.0
    with pytest.raises(ReceiveError):
        receiver.poll()
    assert receiver.phase == "closed" and receiver.buffered_bytes == 0


def test_wrong_peer_and_conversation_cannot_authorize_media():
    receiver = AvReceiver(PEER, 42, 123, COOKIE)
    for peer, conv in (((PEER[0], PEER[1] + 1), 42), (PEER, 43)):
        assert not receiver.receive(packet(0, 0, control(123), conv=conv), peer).acknowledgements
    with pytest.raises(ReceiveError):
        receiver.receive(packet(0, 0, media(header())), PEER)


def test_start_is_not_acceptance_for_an_initiating_session():
    receiver = AvReceiver(PEER, 42, 123, COOKIE)
    start = bytearray(control(123))
    struct.pack_into("<I", start, 8, 6)
    with pytest.raises(ReceiveError):
        receiver.receive(packet(0, 0, bytes(start)), PEER)
    assert receiver.phase == "closed"
