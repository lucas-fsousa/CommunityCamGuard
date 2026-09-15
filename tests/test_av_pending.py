import pytest

from backend.app.drivers.yoosee.p2p import av_receive
from backend.app.drivers.yoosee.p2p.av_handshake import AvHandshake
from backend.app.drivers.yoosee.p2p.av_pending import (
    MAX_PENDING_BYTES,
    MAX_PENDING_MESSAGES,
    PendingAv,
)
from backend.app.drivers.yoosee.p2p.kcp_receive import ReceiveError
from tests.test_av_handshake import ack
from tests.test_av_receive import COOKIE, media
from tests.test_av_receive_channels import CONTROL, receiver, start
from tests.test_captured_media import control, header
from tests.test_media_receive import PEER, packet
from tests.test_v1_receive import av


def test_late_accept_drains_records_once_without_early_decryption(monkeypatch):
    s = receiver()
    calls = []
    original = av_receive.decrypt_media_tlv
    def decrypt(*args):
        calls.append(True)
        return original(*args)
    monkeypatch.setattr(av_receive, "decrypt_media_tlv", decrypt)
    for seq, body in enumerate((start(), media(header()[:13]), media(header()[13:] + av()))):
        result = s.receive(packet(seq, 0, body), PEER)
        assert result.acknowledgements and not result.records
    size = s.buffered_bytes
    s.receive(packet(2, 0, media(header()[13:] + av())), PEER)
    assert s.buffered_bytes == size and not calls
    assert not s.accepted and not s.peer_started and s.phase == "initializing"
    result = s.receive(packet(0, 0, control(123), conv=CONTROL), PEER)
    assert len(result.records) == 2 and result.records[1].video == b"video"
    assert s.phase == "active" and s.buffered_bytes == 0 and len(calls) == 2
    assert not s.receive(packet(0, 0, control(123), conv=CONTROL), PEER).records


@pytest.mark.parametrize("arrival", ["silence", "accept", "duplicate"])
def test_absolute_deadline_closes_both_channels(arrival):
    now = [0.0]
    s = receiver(clock=lambda: now[0])
    s.receive(packet(0, 0, start()), PEER)
    now[0] = 0.9
    s.receive(packet(1, 0, media(header()[:10])), PEER)
    now[0] = 1.0
    with pytest.raises(ReceiveError):
        if arrival == "silence":
            s.poll()
        else:
            s.receive(packet(0, 0, control(123), conv=CONTROL) if arrival == "accept"
                      else packet(0, 0, start()), PEER)
    assert s.phase == "closed" and s.buffered_bytes == 0


@pytest.mark.parametrize("budget", ["bytes", "messages"])
def test_queue_budgets_are_bounded_and_clear_on_failure(budget):
    queue = PendingAv(lambda: 0.0)
    if budget == "bytes":
        queue.append(bytes(MAX_PENDING_BYTES))
    else:
        for _ in range(MAX_PENDING_MESSAGES):
            queue.append(b"x")
    with pytest.raises(ReceiveError, match="budget"):
        queue.append(b"x")
    assert queue.buffered_bytes == 0


def test_overflow_closes_receiver_instead_of_discarding_acknowledged_data():
    s = receiver()
    s.receive(packet(0, 0, start()), PEER)
    for seq in range(1, MAX_PENDING_MESSAGES):
        s.receive(packet(seq, 0, media(b"x")), PEER)
    with pytest.raises(ReceiveError):
        s.receive(packet(MAX_PENDING_MESSAGES, 0, media(b"x")), PEER)
    assert s.phase == "closed" and s.buffered_bytes == 0


def test_wrong_call_accept_cannot_release_pending_media():
    s = receiver()
    s.receive(packet(0, 0, start()), PEER)
    s.receive(packet(1, 0, media(header())), PEER)
    with pytest.raises(ReceiveError):
        s.receive(packet(0, 0, control(124), conv=CONTROL), PEER)
    assert not s.accepted and s.buffered_bytes == 0


def test_handshake_preserves_separate_receipt_conditions_after_reordering():
    s = AvHandshake(PEER, 42, 123, COOKIE, clock=lambda: 0.0)
    init, = s.due()
    s.receive(ack(init), PEER)
    s.receive(packet(0, 0, start()), PEER)
    s.receive(packet(1, 0, media(header() + av())), PEER)
    assert not s.due() and not s.ready
    records = s.receive(packet(0, 0, control(123), conv=CONTROL), PEER).records
    assert len(records) == 2 and not s.ready
    local_start, = s.due()
    s.receive(ack(local_start), PEER)
    assert s.ready and s.buffered_bytes == 0
