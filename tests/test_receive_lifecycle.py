import pytest

from backend.app.drivers.yoosee.p2p.kcp_receive import ReceiveError
from backend.app.drivers.yoosee.p2p.media_protocol import parse_kcp_segments
from backend.app.drivers.yoosee.p2p.receive_lifecycle import ReceiveLifecycle
from tests.test_media_receive import PEER, packet


def test_acknowledged_fragment_survives_initialization_handoff():
    session = ReceiveLifecycle(PEER, 42)
    first = session.receive(packet(0, 1, b"head"), PEER)
    assert first.acknowledgements and not first.messages
    assert session.buffered_bytes == 4 and session.next_sequence == 1
    session.activate()
    result = session.receive(packet(1, 0, b"tail"), PEER)
    assert result.messages == (b"headtail",)
    assert session.buffered_bytes == 0 and session.next_sequence == 2
    assert not session.receive(packet(0, 1, b"head"), PEER).messages


def test_out_of_order_ack_preserves_gap_across_handoff():
    session = ReceiveLifecycle(PEER, 42)
    result = session.receive(packet(1, 0, b"tail"), PEER)
    assert parse_kcp_segments(result.acknowledgements[0])[0].unacknowledged == 0
    session.activate()
    assert session.receive(packet(0, 1, b"head"), PEER).messages == (b"headtail",)


def test_initialization_messages_are_delivered_not_discarded():
    session = ReceiveLifecycle(PEER, 42)
    assert session.receive(packet(0, 0, b"early"), PEER).messages == (b"early",)
    session.activate()
    assert session.receive(packet(1, 0, b"next"), PEER).messages == (b"next",)


def test_conflicting_acknowledged_fragment_closes_whole_lifecycle():
    session = ReceiveLifecycle(PEER, 42)
    session.receive(packet(1, 0, b"tail"), PEER)
    with pytest.raises(ReceiveError, match="conflicting"):
        session.receive(packet(1, 0, b"different"), PEER)
    assert session.phase == "closed" and session.buffered_bytes == 0


def test_repeated_handoff_is_terminal():
    session = ReceiveLifecycle(PEER, 42)
    session.activate()
    with pytest.raises(ReceiveError, match="already activated"):
        session.activate()
    assert session.phase == "closed"


def test_handoff_does_not_reset_fragment_deadline():
    now = [0.0]
    session = ReceiveLifecycle(PEER, 42, clock=lambda: now[0])
    session.receive(packet(0, 1, b"head"), PEER)
    now[0] = 1.9
    session.activate()
    now[0] = 2.0
    with pytest.raises(ReceiveError, match="assembly"):
        session.poll()
    assert session.phase == "closed" and session.buffered_bytes == 0


@pytest.mark.parametrize("traffic", ["duplicate", "wrong_peer", "wrong_conv", "invalid"])
def test_noise_does_not_extend_progress_deadline(traffic):
    now = [0.0]
    session = ReceiveLifecycle(PEER, 42, clock=lambda: now[0], idle_timeout=1)
    session.receive(packet(0, 0, b"first"), PEER)
    session.activate()
    now[0] = 0.9
    wire = packet(0, 0, b"first", conv=43 if traffic == "wrong_conv" else 42)
    peer = (PEER[0], PEER[1] + 1) if traffic == "wrong_peer" else PEER
    session.receive(b"invalid" if traffic == "invalid" else wire, peer)
    now[0] = 1.0
    with pytest.raises(ReceiveError, match="progress"):
        session.poll()
    assert session.phase == "closed"


def test_initialization_deadline_is_absolute_despite_messages():
    now = [0.0]
    session = ReceiveLifecycle(PEER, 42, initialization_timeout=1, clock=lambda: now[0])
    now[0] = 0.9
    session.receive(packet(0, 0, b"data"), PEER)
    now[0] = 1
    with pytest.raises(ReceiveError, match="initialization"):
        session.activate()


def test_lifetime_is_absolute_and_closed_session_cannot_restart():
    now = [0.0]
    session = ReceiveLifecycle(PEER, 42, lifetime=1, clock=lambda: now[0])
    session.activate()
    now[0] = 0.9
    session.receive(packet(0, 0, b"data"), PEER)
    now[0] = 1
    with pytest.raises(ReceiveError, match="lifetime"):
        session.poll()
    for operation in (session.activate, session.poll, lambda: session.receive(packet(1, 0, b"x"), PEER)):
        with pytest.raises(ReceiveError):
            operation()
    session.close()
    replacement = ReceiveLifecycle(PEER, 43, clock=lambda: now[0])
    assert replacement.next_sequence == 0 and replacement.buffered_bytes == 0
    assert not replacement.receive(packet(0, 0, b"old", conv=42), PEER).acknowledgements


@pytest.mark.parametrize("value", [True, 0, -1, float("nan"), float("inf"), 61])
def test_invalid_deadlines(value):
    with pytest.raises(ValueError):
        ReceiveLifecycle(PEER, 42, lifetime=value)
