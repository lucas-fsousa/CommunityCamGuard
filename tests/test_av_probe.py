import struct

import pytest

from backend.app.drivers.yoosee.p2p import av_probe
from backend.app.drivers.yoosee.p2p.av_probe import probe_av_socket
from backend.app.drivers.yoosee.p2p.contracts import CallingAttempt, CallingResult
from backend.app.drivers.yoosee.p2p.kcp_receive import ReceiveError
from backend.app.drivers.yoosee.p2p.media_protocol import KCP_PUSH, parse_kcp_segments
from backend.app.drivers.yoosee.p2p.media_session import MediaChannelResult
from tests.test_av_handshake import ack
from tests.test_av_receive import COOKIE, media
from tests.test_av_receive_channels import CONTROL, start
from tests.test_captured_media import control, header
from tests.test_media_receive import PEER, packet
from tests.test_v1_receive import av

CALLING = CallingResult(True, True, 1, True, None, PEER,
                        attempt=CallingAttempt(42, 123, COOKIE))
CHANNEL = MediaChannelResult(True, True, 1)


class FakeSocket:
    def __init__(self, mode="normal"):
        self.mode = mode
        self.now = 0.0
        self.closed = False
        self.incoming = []
        self.sent = []

    def settimeout(self, timeout):
        assert 0 < timeout <= 0.05
        self.timeout = timeout

    def sendto(self, wire, peer):
        assert peer == PEER
        self.sent.append(wire)
        if self.mode == "send_error":
            raise OSError("private address")
        if self.mode == "partial":
            return 1
        segment, = parse_kcp_segments(wire)
        if segment.command == KCP_PUSH and self.mode != "silence":
            self.incoming.append((ack(wire), PEER))
            if segment.conv == CONTROL:
                # Exercise cross-channel reordering through real handshake code.
                self.incoming.extend((p, PEER) for p in (
                    packet(0, 0, start()), packet(1, 0, media(header() + av())),
                    packet(0, 0, control(123), conv=CONTROL)))
        return len(wire)

    def recvfrom(self, size):
        assert size == 2048
        if self.mode == "receive_error":
            raise OSError("private address")
        if self.incoming:
            self.now += 0.001
            return self.incoming.pop(0)
        self.now += self.timeout
        raise TimeoutError

    def close(self):
        self.closed = True


def run(sock, **kwargs):
    return probe_av_socket(sock, CALLING, CHANNEL, clock=lambda: sock.now, **kwargs)


def test_negotiation_and_reordered_media_are_counted_without_retaining_payload():
    sock = FakeSocket()
    result = run(sock, duration=0.5)
    assert result.ready and result.headers == result.video_frames == 1
    assert result.sent_bytes > 0 and result.peak_buffered_bytes > 0
    assert sock.closed
    controls = [s for wire in sock.sent for s in parse_kcp_segments(wire)
                if s.command == KCP_PUSH]
    assert [struct.unpack_from("<I", s.body, 8)[0] for s in controls] == [1, 6]
    assert [s.body[:4] for s in controls] == [b"\x03\x02\x4c\x00", b"\x03\x00\x4c\x00"]


@pytest.mark.parametrize("mode", ["send_error", "receive_error", "partial", "silence"])
def test_failures_close_socket_without_exposing_network_details(mode):
    sock = FakeSocket(mode)
    with pytest.raises(ReceiveError) as error:
        run(sock)
    assert "private" not in str(error.value) and sock.closed


@pytest.mark.parametrize("duration", [0, -1, 11, float("inf"), float("nan")])
def test_invalid_duration_closes_without_sending(duration):
    sock = FakeSocket()
    with pytest.raises(ValueError):
        run(sock, duration=duration)
    assert sock.closed and not sock.sent


def test_unmetered_route_is_rejected_before_sending():
    sock = FakeSocket()
    with pytest.raises(ValueError):
        probe_av_socket(sock, CALLING, MediaChannelResult(True, False, 0))
    assert sock.closed and not sock.sent


def test_cancellation_before_and_during_reception():
    for after in (0, 0.002):
        sock = FakeSocket()
        with pytest.raises(ReceiveError, match="cancelled"):
            run(sock, cancelled=lambda sock=sock, after=after: sock.now >= after)
        assert sock.closed
        if not after:
            assert not sock.sent


@pytest.mark.parametrize("budget", ["MAX_DATAGRAMS", "MAX_RECEIVED_BYTES", "MAX_SENT_BYTES"])
def test_traffic_budgets_close_session(monkeypatch, budget):
    monkeypatch.setattr(av_probe, budget, 1)
    sock = FakeSocket()
    with pytest.raises(ReceiveError, match="budget"):
        run(sock)
    assert sock.closed


def test_foreign_oversized_and_non_mtp_packets_do_not_receive_acks():
    sock = FakeSocket()
    sock.incoming.extend([(packet(0, 0, start()), ("192.0.2.200", 1)),
                          (b"\xc0\x10" + bytes(2046), PEER), (b"other", PEER)])
    result = run(sock, duration=0.5)
    assert result.ignored_datagrams == 3 and sock.closed


def test_short_deadline_does_not_claim_success_without_video():
    sock = FakeSocket("silence")
    with pytest.raises(ReceiveError, match="without negotiated video"):
        run(sock, duration=0.1)
    assert sock.closed


def test_unrelated_flood_is_also_counted_against_budget(monkeypatch):
    monkeypatch.setattr(av_probe, "MAX_DATAGRAMS", 2)
    sock = FakeSocket()
    sock.incoming.extend([(b"noise", ("192.0.2.200", 1))] * 3)
    with pytest.raises(ReceiveError, match="receive budget"):
        run(sock)
    assert sock.closed


def test_protocol_state_is_cleared_along_with_socket_on_cancellation(monkeypatch):
    sessions = []
    original = av_probe.AvHandshake
    def create(*args, **kwargs):
        session = original(*args, **kwargs)
        sessions.append(session)
        return session
    monkeypatch.setattr(av_probe, "AvHandshake", create)
    sock = FakeSocket()
    with pytest.raises(ReceiveError, match="cancelled"):
        run(sock, cancelled=lambda: sock.now >= 0.004)
    assert sock.closed and len(sessions) == 1
    assert sessions[0].closed and sessions[0].buffered_bytes == 0
