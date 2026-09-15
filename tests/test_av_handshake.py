import pytest

from backend.app.drivers.yoosee.p2p.av_handshake import AvHandshake
from backend.app.drivers.yoosee.p2p.kcp_receive import ReceiveError
from backend.app.drivers.yoosee.p2p.media_protocol import build_kcp_ack, parse_kcp_segments
from tests.test_av_receive import COOKIE, media
from tests.test_av_receive_channels import CONTROL, start
from tests.test_captured_media import control, header
from tests.test_media_receive import PEER, packet
from tests.test_v1_receive import av


def ack(wire):
    segment, = parse_kcp_segments(wire)
    return build_kcp_ack(segment.conv, segment.sequence, segment.timestamp,
                         unacknowledged=segment.sequence + 1)


def session(now=None):
    now = now if now is not None else [0.0]
    return AvHandshake(PEER, 42, 123, COOKIE, clock=lambda: now[0])


def accepted(s):
    init, = s.due()
    s.receive(ack(init), PEER)
    s.receive(packet(0, 0, control(123), conv=CONTROL), PEER)
    s.receive(packet(0, 0, start()), PEER)
    local_start, = s.due()
    return local_start


def test_end_to_end_negotiation_keeps_early_media_and_late_start_receipt_separate():
    s = session()
    local_start = accepted(s)
    records = s.receive(packet(1, 0, media(header() + av()[:20])), PEER).records
    assert len(records) == 1 and records[0].encoding is not None and not s.ready
    s.receive(ack(local_start), PEER)
    assert s.ready and s.buffered_bytes == 20
    result = s.receive(packet(2, 0, media(av()[20:])), PEER)
    assert result.records[0].video == b"video" and s.buffered_bytes == 0
    assert not s.due()
    assert not s.receive(packet(2, 0, media(av()[20:])), PEER).records


def test_ack_alone_and_accept_alone_do_not_emit_start():
    s = session()
    init, = s.due()
    s.receive(ack(init), PEER)
    assert not s.due() and not s.ready
    s.receive(packet(0, 0, control(123), conv=CONTROL), PEER)
    assert not s.due() and not s.ready
    s.receive(packet(0, 0, start()), PEER)
    assert len(s.due()) == 1 and not s.ready


def test_accept_does_not_replace_missing_init_receipt():
    now = [0.0]
    s = session(now)
    init, = s.due()
    s.receive(packet(0, 0, control(123), conv=CONTROL), PEER)
    s.receive(packet(0, 0, start()), PEER)
    now[0] = 0.25
    assert s.due() == (init,)
    s.receive(ack(init), PEER)
    sent, = s.due()
    assert parse_kcp_segments(sent)[0].conv == 42


def test_missing_start_receipt_closes_receivers_even_with_media():
    now = [0.0]
    s = session(now)
    accepted(s)
    s.receive(packet(1, 0, media(header() + av()[:20])), PEER)
    now[0] = 2
    with pytest.raises(ReceiveError):
        s.poll()
    assert s.closed and not s.ready and s.buffered_bytes == 0


def test_receipts_without_media_do_not_establish_readiness():
    now = [0.0]
    s = session(now)
    s.receive(ack(accepted(s)), PEER)
    assert not s.ready
    now[0] = 5
    with pytest.raises(ReceiveError, match="readiness"):
        s.poll()
    assert s.closed


@pytest.mark.parametrize("fault", ["wrong_call", "media_before_start", "bad_media"])
def test_protocol_failure_closes_senders_and_parser(fault):
    s = session()
    s.due()
    with pytest.raises(ReceiveError):
        if fault == "wrong_call":
            s.receive(packet(0, 0, control(124), conv=CONTROL), PEER)
        elif fault == "media_before_start":
            s.receive(packet(0, 0, media(header())), PEER)
        else:
            s.receive(packet(0, 0, control(123), conv=CONTROL), PEER)
            s.receive(packet(0, 0, start()), PEER)
            s.receive(packet(1, 0, media(b"invalid" * 5)), PEER)
    assert s.closed and s.buffered_bytes == 0
    with pytest.raises(ReceiveError):
        s.due()


def test_unsolicited_and_wrong_peer_traffic_cannot_authorize_start():
    s = session()
    assert not s.receive(packet(0, 0, control(123), conv=CONTROL), PEER).acknowledgements
    init, = s.due()
    s.receive(ack(init), (PEER[0], PEER[1] + 1))
    assert not s.ready and not s.due()
    s.close()
    s.close()
    with pytest.raises(ReceiveError):
        s.receive(ack(init), PEER)
