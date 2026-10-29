"""Continuous driver admission and real protocol workers over simulated sockets."""

from dataclasses import replace

import pytest

from backend.app.drivers.yoosee import shared_read_session as module
from backend.app.drivers.yoosee.p2p import account_read_worker, control_ownership
from backend.app.drivers.yoosee.p2p.account_policy import AccountPolicy
from backend.app.drivers.yoosee.p2p.account_read_lifecycle import (
    AccountReadLifecycle,
    ReadConnection,
)
from backend.app.drivers.yoosee.p2p.account_read_worker import WorkerSummary
from backend.app.drivers.yoosee.p2p.cooperative_tcp_media import CooperativeTcpMedia, TcpMediaResult
from backend.app.drivers.yoosee.p2p.media_policy import MediaPolicy
from backend.app.drivers.yoosee.p2p.media_route_job import MediaRouteResult
from backend.app.drivers.yoosee.p2p.video_delivery import VideoDelivery
from tests.test_account_media_route import SPEC, MediaSocket
from tests.test_broker_reads import NODE
from tests.test_continuous_media import session as tcp_session
from tests.test_cooperative_tcp_media import OwnedSocket
from tests.test_media_receive import PEER
from tests.test_read_preparation import ENROLLMENT
from tests.test_read_preparation import owner as owner


@pytest.mark.parametrize('released', [True, False])
def test_stopped_media_accepts_only_confirmed_outer_retirement(setup, monkeypatch, released):
    session, delivery, *_ = setup
    summary = WorkerSummary('stopped', 0, 0, 0, False,
        media=MediaRouteResult(True, True, released),
        tcp=TcpMediaResult(True, False, False, 10, 10, 1))
    def stopped(*args, **kwargs):
        session._lifecycle._state = 'blocked'
        return summary
    monkeypatch.setattr(session._lifecycle, 'run_media', stopped)
    if released:
        result = session.run(cancelled=lambda: False, video_delivery=delivery)
        assert result.reason == 'stopped' and result.worker == summary
        assert not control_ownership._cleanup_uncertain
    else:
        with pytest.raises(module.P2PProbeError, match='cleanup unconfirmed'):
            session.run(cancelled=lambda: False, video_delivery=delivery)
        assert control_ownership._cleanup_uncertain


def test_failed_measurement_is_transport_failure_not_permanent_cleanup_poison(setup, monkeypatch):
    session, delivery, *_ = setup
    summary = WorkerSummary('protocol', 0, 0, 0, False,
        media=MediaRouteResult(True, True, True),
        tcp=TcpMediaResult(False, False, False, 0, 0, 0), protocol_code='tcp_session_timeout')
    def failed(*args, **kwargs):
        session._lifecycle._state = 'blocked'
        return summary
    monkeypatch.setattr(session._lifecycle, 'run_media', failed)
    result = session.run(cancelled=lambda: False, video_delivery=delivery)
    assert result.reason == 'transport' and not control_ownership._cleanup_uncertain


@pytest.fixture
def setup(monkeypatch, owner):
    now = [1.0]
    udp, tcp = MediaSocket(), OwnedSocket(now)
    tcp_owner = tcp_session(now)
    delivery = VideoDelivery()
    pump = CooperativeTcpMedia(tcp, PEER, tcp_owner, clock=lambda: now[0], video=delivery)
    events = []
    entry = replace(ENROLLMENT, access_id=SPEC.access_id)
    monkeypatch.setattr(module.p2p, "read_registered_enrollment", lambda _: entry)

    def connect(*args, **kwargs):
        assert owner._paused and not kwargs["cancelled"]()
        events.append("auth")
        return ReadConnection(NODE, udp, "192.0.2.1", 1234, SPEC.device)

    monkeypatch.setattr(module, "open_read_connection", connect)
    original_tcp_recv = tcp.recv

    def tcp_recv(size):
        try:
            return original_tcp_recv(size)
        finally:
            now[0] -= 0.049

    monkeypatch.setattr(tcp, "recv", tcp_recv)
    original_udp_recv = udp.recvfrom

    def udp_recv(size):
        udp.now = now[0]
        try:
            return original_udp_recv(size)
        finally:
            now[0] = udp.now

    monkeypatch.setattr(udp, "recvfrom", udp_recv)

    def open_tcp(job, spec, **kwargs):
        assert owner._paused and job.endpoint is not None and spec == SPEC
        assert kwargs["policy"] is MediaPolicy.CONTINUOUS and kwargs["video"] is delivery
        events.append("tcp_open")
        return pump

    monkeypatch.setattr(account_read_worker, "open_tcp_media", open_tcp)
    tcp_close, udp_close, udp_send = tcp.close, udp.close, udp.sendto

    def close_tcp():
        assert owner._paused
        events.append("tcp_close")
        tcp_close()

    def send(wire, peer):
        if wire[1] == 0xB9:
            assert tcp.closed == 1 and owner._paused
            events.append("broker_close")
        return udp_send(wire, peer)

    def close_udp():
        assert owner._paused
        events.append("udp_close")
        udp_close()

    monkeypatch.setattr(tcp, "close", close_tcp)
    monkeypatch.setattr(udp, "sendto", send)
    monkeypatch.setattr(udp, "close", close_udp)
    session = module.SharedReadSession(entry.camera_id, entry.device_id, clock=lambda: now[0])
    return session, delivery, tcp_owner, udp, tcp, events, entry, now


@pytest.mark.parametrize("reason", ["stopped", "consumer_closed", "contended", "enrollment_changed"])
def test_continuous_media_yields_and_closes_before_releasing_account(setup, monkeypatch, owner, reason):
    session, delivery, tcp_owner, udp, tcp, events, entry, now = setup
    if reason == "contended":
        monkeypatch.setattr(module, "control_channel_contended", lambda: tcp_owner.ready)
    if reason == "enrollment_changed":
        monkeypatch.setattr(module.p2p, "read_registered_enrollment", lambda _: (
            replace(entry, access_token=b"changed") if tcp_owner.ready else entry))

    def cancelled():
        if tcp_owner.ready and reason == "consumer_closed":
            delivery.close()
        return (tcp_owner.ready and reason == "stopped") or now[0] > 10

    result = session.run(cancelled=cancelled, video_delivery=delivery)
    assert result.reason == reason
    assert result.worker.tcp.close_acknowledged and result.worker.media.broker_release
    assert events == ["auth", "tcp_open", "tcp_close", "broker_close", "udp_close"]
    assert udp.closed == tcp.closed == 1 and delivery.closed
    assert not owner._paused and not control_ownership._cleanup_uncertain


def test_stopped_stream_without_inner_receipt_releases_after_outer_ack(setup):
    session, delivery, tcp_owner, udp, tcp, events, _, _ = setup
    tcp.fault = 'close'
    result = session.run(cancelled=lambda: tcp_owner.ready, video_delivery=delivery)
    assert result.reason == 'stopped' and not result.worker.tcp.close_acknowledged
    assert result.worker.media.broker_release and udp.closed == tcp.closed == 1
    assert events == ['auth', 'tcp_open', 'tcp_close', 'broker_close', 'udp_close']
    assert not control_ownership._cleanup_uncertain


@pytest.mark.parametrize("fault", ["tcp_close", "broker_ack", "udp_close"])
def test_unconfirmed_media_cleanup_blocks_legacy_auth(setup, fault):
    session, delivery, tcp_owner, udp, tcp, events, _, _ = setup
    if fault == "tcp_close": tcp.fault = "physical_close"
    if fault == "broker_ack": udp.media_fault = "silent"
    if fault == "udp_close": udp.fault = "close"
    with pytest.raises(module.P2PProbeError, match="cleanup"):
        session.run(cancelled=lambda: tcp_owner.ready, video_delivery=delivery)
    assert events.count("auth") == 1 and delivery.closed
    assert control_ownership._cleanup_uncertain
    with pytest.raises(control_ownership.ControlChannelBusy):
        with control_ownership.own_control_channel():
            pytest.fail("unsafe new authentication")


def test_cancel_before_start_retires_queue_without_auth(setup):
    session, delivery, _, _, _, events, _, _ = setup
    session.stop()
    assert session.run(cancelled=lambda: False, video_delivery=delivery).reason == "stopped"
    assert not events and delivery.closed


def test_finite_lifecycle_rejects_media_before_authentication():
    lifecycle = AccountReadLifecycle()
    delivery = VideoDelivery()
    with pytest.raises(ValueError, match="continuous"):
        lifecycle.run_media(lambda _: pytest.fail("authentication"), media=lambda _: SPEC,
                            delivery=delivery, cancelled=lambda: False)
    assert lifecycle.state == "idle" and not delivery.closed


def test_concurrent_run_rejection_does_not_close_active_queue():
    lifecycle = AccountReadLifecycle(policy=AccountPolicy.CONTINUOUS)
    delivery = VideoDelivery()
    # The connecting generation owns the queue; a second run must not retire it.
    lifecycle._state = "connecting"
    with pytest.raises(ValueError, match="busy"):
        lifecycle.run_media(lambda _: pytest.fail("authentication"), media=lambda _: SPEC,
                            delivery=delivery, cancelled=lambda: False)
    assert not delivery.closed
