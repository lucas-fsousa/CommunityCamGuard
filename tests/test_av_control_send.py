import struct

import pytest

from backend.app.drivers.yoosee.p2p.av_control_send import ReliableAvControl
from backend.app.drivers.yoosee.p2p.kcp_receive import ReceiveError
from backend.app.drivers.yoosee.p2p.media_protocol import (
    KCP_HEADER,
    build_kcp_ack,
    build_kcp_push,
    build_mtp_frame,
    parse_kcp_segments,
)
from tests.test_media_receive import PEER


@pytest.mark.parametrize("action,conv", [(1, 42 | 0x80000000), (6, 42)])
def test_control_uses_own_conversation_and_transport_receipt_only(action, conv):
    sender = ReliableAvControl(PEER, 42, 123, action=action, clock=lambda: 1.0)
    wire = sender.due()
    segment, = parse_kcp_segments(wire)
    assert segment.conv == conv and segment.sequence == 0
    assert struct.unpack_from("<I", segment.body, 8)[0] == action
    assert sender.receive_ack(build_kcp_ack(conv, 0, 1000, unacknowledged=1), PEER)
    assert sender.acknowledged and sender.due() is None
    assert not sender.receive_ack(build_kcp_ack(conv, 0, 1000, unacknowledged=1), PEER)


def test_identical_retransmissions_are_bounded_and_expire_during_silence():
    now = [0.0]
    sender = ReliableAvControl(PEER, 42, 123, action=1, clock=lambda: now[0])
    first = sender.due()
    assert sender.due() is None
    for value in (0.25, 0.5, 0.75):
        now[0] = value
        assert sender.due() == first
    now[0] = 1
    assert sender.due() is None and sender.attempts == 4
    now[0] = 2
    with pytest.raises(ReceiveError, match="deadline"):
        sender.poll()
    assert sender.closed
    with pytest.raises(ReceiveError, match="closed"):
        sender.due()


@pytest.mark.parametrize("fault", ["unsent", "peer", "conv", "sequence", "timestamp",
                                   "checksum", "oversize", "push", "body", "fragment"])
def test_unrelated_or_malformed_ack_cannot_complete_send(fault):
    sender = ReliableAvControl(PEER, 42, 123, action=1, clock=lambda: 1.0)
    if fault != "unsent":
        sender.due()
    conv = 43 if fault == "conv" else 42 | 0x80000000
    wire = build_kcp_ack(conv, 1 if fault == "sequence" else 0,
                         999 if fault == "timestamp" else 1000, unacknowledged=999)
    peer = (PEER[0], 1) if fault == "peer" else PEER
    if fault == "checksum":
        wire = wire[:4] + bytes([wire[4] ^ 1]) + wire[5:]
    elif fault == "oversize":
        wire = bytes(2048)
    elif fault == "push":
        wire = build_kcp_push(conv, 0, b"body", timestamp=1000)
    elif fault in ("body", "fragment"):
        body = b"x" if fault == "body" else b""
        wire = build_mtp_frame(0x10, KCP_HEADER.pack(conv, 0x52, int(fault == "fragment"),
                                                  1, 1000, 0, 1, len(body)) + body)
    assert not sender.receive_ack(wire, peer) and not sender.acknowledged


def test_receipt_after_deadline_does_not_revive_request():
    now = [0.0]
    sender = ReliableAvControl(PEER, 42, 123, action=6, clock=lambda: now[0])
    sender.due()
    now[0] = 2
    with pytest.raises(ReceiveError):
        sender.receive_ack(build_kcp_ack(42, 0, 0, unacknowledged=1), PEER)
    assert not sender.acknowledged and sender.closed


def test_timestamp_wrap_and_coalesced_ack_segments():
    sender = ReliableAvControl(PEER, 42, 123, action=6, clock=lambda: (2**32 + 5) / 1000)
    segment, = parse_kcp_segments(sender.due())
    assert segment.timestamp == 5
    wrong = build_kcp_ack(43, 0, 5, unacknowledged=1)
    correct = build_kcp_ack(42, 0, 5, unacknowledged=1)
    assert sender.receive_ack(build_mtp_frame(0x10, wrong[6:] + correct[6:]), PEER)


def test_cancellation_prevents_all_later_emission():
    sender = ReliableAvControl(PEER, 42, 123, action=1)
    sender.close()
    sender.close()
    with pytest.raises(ReceiveError):
        sender.due()
    assert sender.attempts == 0


@pytest.mark.parametrize("action", [0, 2, 3, True, 7])
def test_other_controls_are_not_supported(action):
    with pytest.raises(ValueError):
        ReliableAvControl(PEER, 42, 123, action=action)
