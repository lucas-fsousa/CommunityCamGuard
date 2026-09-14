import pytest

from backend.app.drivers.yoosee.p2p.kcp_receive import KcpReceiver
from backend.app.drivers.yoosee.p2p.media_protocol import (
    KCP_HEADER,
    KCP_PUSH,
    build_kcp_ack,
    build_mtp_frame,
    parse_kcp_segments,
)
from backend.app.drivers.yoosee.p2p.media_receive import MediaReceiver

PEER = ("192.0.2.10", 50000)


def packet(sequence, fragment, body, conv=42):
    return build_mtp_frame(0x10, KCP_HEADER.pack(
        conv, KCP_PUSH, fragment, 128, 100, sequence, 0, len(body),
    ) + body)


def test_wire_ack_preserves_gap_and_advertises_bounded_window():
    receiver = MediaReceiver(PEER, KcpReceiver(42, max_segments=8))
    result = receiver.receive(packet(1, 0, b"tail"), PEER)
    ack, = parse_kcp_segments(result.acknowledgements[0])
    assert (ack.sequence, ack.unacknowledged, ack.window) == (1, 0, 7)
    assert result.messages == ()
    result = receiver.receive(packet(0, 1, b"head"), PEER)
    ack, = parse_kcp_segments(result.acknowledgements[0])
    assert (ack.sequence, ack.unacknowledged, ack.window) == (0, 2, 8)
    assert result.messages == (b"headtail",)


@pytest.mark.parametrize("case", ["peer", "conv", "checksum", "oversize", "truncated"])
def test_untrusted_datagrams_never_ack_or_allocate(case):
    receiver = MediaReceiver(PEER, KcpReceiver(42))
    peer = PEER
    wire = packet(0, 0, b"data")
    if case == "peer":
        peer = (PEER[0], PEER[1] + 1)
    elif case == "conv":
        wire = packet(0, 0, b"data", conv=43)
    elif case == "checksum":
        altered = bytearray(wire)
        altered[4] ^= 1
        wire = bytes(altered)
    elif case == "oversize":
        wire = bytes(2048)
    else:
        wire = wire[:-1]
    result = receiver.receive(wire, peer)
    assert not result.acknowledgements and not result.messages
    assert receiver.receiver.buffered_bytes == 0


@pytest.mark.parametrize("window", [-1, 65536, True])
def test_ack_rejects_invalid_window(window):
    with pytest.raises(ValueError):
        build_kcp_ack(42, 0, 0, unacknowledged=0, window=window)
