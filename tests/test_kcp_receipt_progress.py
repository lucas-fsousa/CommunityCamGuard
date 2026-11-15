"""Selective receipt and peer cumulative progress must remain distinct facts."""

import pytest

from backend.app.drivers.yoosee.p2p.media_protocol import build_kcp_ack, parse_kcp_segments
from backend.app.drivers.yoosee.p2p.reliable_kcp_send import ReliableKcpPush
from tests.test_media_receive import PEER


@pytest.mark.parametrize('peer_next', [0, 4, 5, 0xffffffff])
def test_correlated_ack_retains_peer_progress_without_reinterpreting_delivery(peer_next):
    request = ReliableKcpPush(PEER, 42, 4, b'bounded', clock=lambda: 0.)
    assert request.peer_next_sequence is None and request.sequence == 4
    wire = request.due()
    segment, = parse_kcp_segments(wire)
    receipt = build_kcp_ack(42, 4, segment.timestamp, unacknowledged=peer_next)
    assert request.receive_ack(receipt, PEER) is True
    assert request.acknowledged and request.peer_next_sequence == peer_next
    assert request.due() is None  # Observation alone cannot cause a replay.


def test_unrelated_receipt_cannot_supply_progress():
    request = ReliableKcpPush(PEER, 42, 4, b'bounded', clock=lambda: 0.)
    request.due()
    assert not request.receive_ack(build_kcp_ack(42, 3, 0, unacknowledged=99), PEER)
    assert not request.acknowledged and request.peer_next_sequence is None
