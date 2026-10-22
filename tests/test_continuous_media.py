import pytest

from backend.app.drivers.yoosee.p2p.cooperative_tcp_media import CooperativeTcpMedia
from backend.app.drivers.yoosee.p2p.kcp_receive import ReceiveError
from backend.app.drivers.yoosee.p2p.media_policy import MediaPolicy, MediaRateBudget
from backend.app.drivers.yoosee.p2p.mtp_tcp_av import TcpAvSession
from tests.test_av_handshake import ack
from tests.test_av_receive import COOKIE, media
from tests.test_cooperative_tcp_media import OwnedSocket
from tests.test_media_receive import PEER, packet
from tests.test_v1_receive import av
from tests.test_vendor_mtp_tcp_av import canonical, incoming, measurement_ack, negotiate


def session(now):
    return TcpAvSession(PEER, 42, 123, COOKIE, source_id=17, destination_id=23,
                        policy=MediaPolicy.CONTINUOUS, clock=lambda: now[0])


def test_fifteen_virtual_minutes_one_session_beyond_cumulative_limits():
    now = [1.0]
    owner = session(now)
    negotiate(owner)
    receiver = owner._av
    for seq in range(2, 4502):
        now[0] += 0.2
        batch = owner.receive(incoming(packet(seq, 0, media(av(video=b"x" * 700,
                                                              video_ts=seq)))), PEER)
        assert len(batch.records) == 1 and owner.buffered_bytes == 0
        assert owner._av is receiver and owner.ready
        owner.poll()
    assert owner.received_bytes > 2 * 1024 * 1024 and owner.received_records > 4096
    owner.begin_finish()
    close, = owner.due()
    owner.receive(incoming(ack(canonical(close))), PEER)
    assert owner.close_acknowledged
    owner.close()
    assert owner.closed and owner.buffered_bytes == 0


def test_continuous_session_keeps_measurement_and_idle_deadlines():
    now = [1.0]
    owner = session(now)
    owner.due()
    now[0] = 4
    with pytest.raises(ReceiveError): owner.receive(measurement_ack(), PEER)
    assert owner.closed
    now[0] = 1
    owner = session(now)
    negotiate(owner)
    # Duplicates and measurement ACKs cannot keep a stalled video conversation alive.
    for _ in range(4):
        now[0] += 1
        owner.receive(measurement_ack(), PEER)
    now[0] += 1
    with pytest.raises(ReceiveError): owner.poll()
    assert owner.closed and owner.buffered_bytes == 0


def test_rate_exhaustion_retires_actual_tcp_session():
    now = [1.0]
    owner = session(now)
    negotiate(owner)
    now[0] += 1
    owner._traffic.charge(rx=2 * 1024 * 1024)
    with pytest.raises(ReceiveError):
        owner.receive(measurement_ack(), PEER)
    assert owner.closed and owner.buffered_bytes == 0


@pytest.mark.parametrize("policy", [True, "continuous", None])
def test_media_policy_is_typed_not_truthy(policy):
    with pytest.raises(ValueError):
        TcpAvSession(PEER, 42, 123, COOKIE, source_id=17, destination_id=23, policy=policy)


@pytest.mark.parametrize("field, capacity", [("rx", 2 * 1024 * 1024), ("tx", 256 * 1024),
                                           ("records", 4096)])
def test_media_rate_refills_but_never_banks_idle_credit(field, capacity):
    now = [0.0]
    budget = MediaRateBudget(clock=lambda: now[0])
    budget.charge(**{field: capacity})
    with pytest.raises(ValueError): budget.charge(**{field: 1})
    now[0] = 0.5
    budget.charge(**{field: capacity // 2})
    now[0] = 3600
    with pytest.raises(ValueError): budget.charge(**{field: capacity + 1})
    budget.charge(**{field: capacity})


@pytest.mark.parametrize("clock", [-1, float("nan"), float("inf")])
def test_media_budget_rejects_broken_clock(clock):
    now = [0.0]
    budget = MediaRateBudget(clock=lambda: now[0])
    now[0] = clock
    with pytest.raises(ValueError): budget.charge(rx=1)


def test_continuous_pump_does_not_stop_at_finite_observation_window():
    now = [1.0]
    owner = session(now)
    class Socket(OwnedSocket):
        sequence = 2
        def recv(self, size):
            if not self.pending and owner.ready:
                self.pending.extend(incoming(packet(self.sequence, 0, media(av(video_ts=self.sequence)))))
                self.sequence += 1
            return super().recv(size)
    sock = Socket(now)
    pump = CooperativeTcpMedia(sock, PEER, owner, clock=lambda: now[0])
    while now[0] < 65:
        pump.step()
        assert pump.result is None
    result = pump.finish()
    assert result.ready and result.close_acknowledged and sock.closed == 1
    assert result.video_frames > 20 and sock.actions == [1, 6, 7]
