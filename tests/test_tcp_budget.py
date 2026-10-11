from dataclasses import FrozenInstanceError

import pytest

from backend.app.drivers.yoosee.p2p.av_sample import AvVideoSample
from backend.app.drivers.yoosee.p2p.broker_maintenance import BrokerMaintenance
from backend.app.drivers.yoosee.p2p.kcp_receive import ReceiveError
from backend.app.drivers.yoosee.p2p.mtp_tcp_av import TcpAvSession
from backend.app.drivers.yoosee.p2p.mtp_tcp_receive import receive_paired_av
from backend.app.drivers.yoosee.p2p.tcp_budget import TcpProfile, validate_profile
from tests.test_av_receive import COOKIE, media
from tests.test_broker_maintenance import NODE
from tests.test_media_receive import PEER, packet
from tests.test_mtp_tcp_receive import Socket
from tests.test_v1_receive import av
from tests.test_vendor_mtp_tcp_av import incoming, negotiate


def owner(now, profile=TcpProfile.EXTENDED):
    return TcpAvSession(PEER, 42, 123, COOKIE, source_id=17, destination_id=23,
                        clock=lambda: now[0], profile=profile)


@pytest.mark.parametrize("value", [None, "extended", 10, {}, TcpProfile.EXTENDED.budget])
def test_only_reviewed_profiles_are_accepted(value):
    with pytest.raises(ValueError, match="unreviewed"):
        validate_profile(value)


def test_profiles_are_immutable_and_nested_deadlines_leave_cleanup_room():
    for profile in TcpProfile:
        b = profile.budget
        with pytest.raises(FrozenInstanceError):
            b.receive_seconds = 999
        assert b.observation_seconds + 2 < b.receive_seconds < b.session_seconds
        assert 4 + 2 + b.receive_seconds < b.broker_seconds
    assert 20 + TcpProfile.EXTENDED.budget.broker_seconds <= TcpProfile.EXTENDED.budget.route_seconds


def test_ten_seconds_of_media_closes_without_retained_history():
    now = [1.0]
    session = owner(now)

    class Continuous(Socket):
        sequence = 2
        peak = 0
        def recv(self, size):
            if not self.pending and 6 in self.actions and 7 not in self.actions:
                self.pending.extend(incoming(packet(self.sequence, 0, media(av()))))
                self.sequence += 1
            result = super().recv(size)
            self.peak = max(self.peak, session.buffered_bytes)
            return result

    sock = Continuous(now)
    result = receive_paired_av(sock, PEER, session, clock=lambda: now[0])
    assert result.ready and result.close_acknowledged and result.video_frames > 20
    assert result.timing.observation_ms >= 10000
    assert sock.actions == [1, 6, 7] and sock.peak < 4096
    assert session.closed and session.buffered_bytes == 0


def test_longer_profile_does_not_extend_startup_or_idle():
    now = [1.0]
    session = owner(now)
    session.due()
    now[0] = 4.0
    with pytest.raises(ReceiveError):
        session.poll()
    now[0] = 1.0
    session = owner(now)
    negotiate(session)
    now[0] += 5.0
    with pytest.raises(ReceiveError):
        session.poll()
    assert session.closed


def test_broker_extended_deadline_does_not_extend_packet_budget():
    now = [0.0]
    broker = BrokerMaintenance(NODE, clock=lambda: now[0], profile=TcpProfile.EXTENDED)
    now[0] = 16.0
    assert broker.receive(b"", NODE.address) == ()
    for _ in range(511):
        broker.receive(b"", NODE.address)
    with pytest.raises(ValueError, match="budget"):
        broker.receive(b"", NODE.address)
    assert broker.closed


def test_extended_receiver_rejects_sample_and_cleans_owner():
    now = [1.0]
    session, sample = owner(now), AvVideoSample()
    sample.data.extend(b"private")
    sock = Socket(now)
    with pytest.raises(ReceiveError, match="decode sample"):
        receive_paired_av(sock, PEER, session, clock=lambda: now[0], sample=sample)
    assert not sock.writes and session.closed and sample.closed and not sample.data


def test_extended_receiver_cancellation_never_sends_start():
    now = [1.0]
    session, sock = owner(now), Socket(now)
    with pytest.raises(ReceiveError, match="cancelled"):
        receive_paired_av(sock, PEER, session, clock=lambda: now[0], cancelled=lambda: True)
    assert not sock.writes and session.closed
