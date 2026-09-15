import struct

import pytest

from backend.app.drivers.yoosee.p2p.av_control_send import ReliableAvControl
from backend.app.drivers.yoosee.p2p.kcp_receive import ReceiveError
from backend.app.drivers.yoosee.p2p.media_protocol import build_kcp_ack, parse_kcp_segments
from tests.test_av_handshake import accepted, ack, session
from tests.test_av_probe import FakeSocket, run
from tests.test_av_receive import media
from tests.test_captured_media import header
from tests.test_media_receive import PEER, packet
from tests.test_v1_receive import av


def ready(now):
    s = session(now)
    s.receive(ack(accepted(s)), PEER)
    s.receive(packet(1, 0, media(header() + av()[:20])), PEER)
    assert s.ready and s.buffered_bytes == 20
    return s


def test_close_retains_receiver_and_allocates_only_one_outgoing_sequence():
    now = [0.0]
    s = ready(now)
    s.begin_finish()
    assert not s.ready and not s.close_acknowledged and s.buffered_bytes == 20
    first, = s.due()
    segment, = parse_kcp_segments(first)
    assert segment.conv == 42 and segment.sequence == 1
    assert struct.unpack_from("<I", segment.body, 8)[0] == 7
    assert s.receive(packet(2, 0, media(av()[20:])), PEER).records[0].video == b"video"
    for tick in (0.25, 0.5, 0.75):
        now[0] = tick
        s.begin_finish()  # Idempotent, including its original absolute deadline.
        assert s.due() == (first,)
    s.receive(ack(first), PEER)
    assert s.close_acknowledged and not s.due()
    s.close()
    assert s.closed and not s.close_acknowledged and s.buffered_bytes == 0


@pytest.mark.parametrize("fault", ["unsent", "start_ack", "peer", "timestamp", "conv"])
def test_close_requires_its_exact_sent_receipt(fault):
    now = [0.0]
    s = ready(now)
    s.begin_finish()
    if fault != "unsent":
        s.due()
    wire = build_kcp_ack(43 if fault == "conv" else 42,
                         0 if fault == "start_ack" else 1,
                         1 if fault == "timestamp" else 0, unacknowledged=999)
    s.receive(wire, (PEER[0], 1) if fault == "peer" else PEER)
    assert not s.close_acknowledged


def test_close_timeout_is_terminal_and_late_ack_cannot_revive_it():
    now = [0.0]
    s = ready(now)
    s.begin_finish()
    wire, = s.due()
    now[0] = 2.0
    with pytest.raises(ReceiveError):
        s.receive(ack(wire), PEER)
    assert s.closed and not s.close_acknowledged and s.buffered_bytes == 0


def test_close_cannot_be_started_before_full_negotiation():
    s = session()
    with pytest.raises(ReceiveError, match="completed negotiation"):
        s.begin_finish()
    assert not s.close_acknowledged


@pytest.mark.parametrize("action,sequence", [(1, 1), (6, 1), (7, 0), (7, 2), (7, True)])
def test_sender_cannot_reuse_start_sequence_or_allocate_arbitrary_sequences(action, sequence):
    with pytest.raises(ValueError, match="sequence"):
        ReliableAvControl(PEER, 42, 123, action=action, sequence=sequence)


class MissingCloseSocket(FakeSocket):
    def sendto(self, wire, peer):
        segment, = parse_kcp_segments(wire)
        if segment.sequence == 1 and segment.body and struct.unpack_from("<I", segment.body, 8)[0] == 7:
            self.sent.append(wire)
            return len(wire)
        return super().sendto(wire, peer)


def test_probe_close_failure_has_bounded_retries_and_closes_local_socket():
    sock = MissingCloseSocket()
    with pytest.raises(ReceiveError, match="deadline"):
        run(sock, duration=0.5)
    assert sock.closed and sock.now <= 2.56
    closes = [w for w in sock.sent if parse_kcp_segments(w)[0].sequence == 1
              and parse_kcp_segments(w)[0].body]
    assert len(closes) == 4 and len(set(closes)) == 1


def test_cancellation_during_close_does_not_wait_for_remaining_retries():
    sock = MissingCloseSocket()
    with pytest.raises(ReceiveError, match="cancelled"):
        run(sock, duration=0.5, cancelled=lambda: sock.now >= 0.6)
    assert sock.closed and sock.now < 0.7
