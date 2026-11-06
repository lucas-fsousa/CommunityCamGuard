"""One synthetic UDP reader pumps three independent TCP handoffs; no real I/O."""

from dataclasses import replace

import pytest

from backend.app.drivers.yoosee.p2p.account_media_channel import (
    MediaBootstrap,
    MediaChannelSummary,
    media_channels_closed,
)
from backend.app.drivers.yoosee.p2p.account_policy import AccountPolicy
from backend.app.drivers.yoosee.p2p.account_read_lifecycle import AccountReadLifecycle
from backend.app.drivers.yoosee.p2p.account_read_worker import AccountReadWorker, WorkerSummary
from backend.app.drivers.yoosee.p2p.cooperative_tcp_media import TcpMediaResult
from backend.app.drivers.yoosee.p2p.media_policy import MediaPolicy
from backend.app.drivers.yoosee.p2p.media_retirement import retired_media_route
from backend.app.drivers.yoosee.p2p.media_route_job import MediaRouteResult, MediaRouteSpec
from backend.app.drivers.yoosee.p2p.video_delivery import VideoDelivery
from tests.test_account_media_route import MediaSocket
from tests.test_account_read_lifecycle import connection
from tests.test_broker_reads import DEVICE, NODE


def admissions():
    return tuple(MediaBootstrap(MediaRouteSpec(123, replace(DEVICE, device_id=100 + index)),
                                VideoDelivery()) for index in range(3))


class Tcp:
    def __init__(self, bootstrap, events, fail=None):
        self.bootstrap, self.events, self.fail = bootstrap, events, fail
        self.ready = True
        self.result = None
        self.steps = self.finishes = 0

    def step(self, *, budget_seconds=0.2):
        assert 0 < budget_seconds <= 0.2 / 3
        self.steps += 1
        self.events.append(("step", self.bootstrap.spec.device.device_id))
        if self.fail == "step" and self.steps == 3:
            self.result = TcpMediaResult(True, False, False, self.steps, 0, 0)
            raise OSError("synthetic closed transport")

    def finish(self):
        self.finishes += 1
        self.events.append(("finish", self.bootstrap.spec.device.device_id))
        self.result = TcpMediaResult(True, True, False, self.steps, 0, 0)
        if self.fail == "finish":
            raise OSError("synthetic cleanup error")
        return self.result


def run(monkeypatch, *, fail=None, duration=0.1):
    sock, entries, events, pumps = MediaSocket(), admissions(), [], []
    owner = AccountReadWorker(NODE, 1, "192.0.2.1", 1234, clock=lambda: sock.now,
                              policy=AccountPolicy.CONTINUOUS, media_capacity=3)
    def opener(job, spec, *, clock, video, policy):
        assert policy is MediaPolicy.CONTINUOUS
        entry = next(item for item in entries if item.spec is spec)
        assert video is entry.video and job.endpoint is not None
        failure = "finish" if fail == spec.device.device_id else "step" if fail == "step100" and spec.device.device_id == 100 else None
        tcp = Tcp(entry, events, fail=failure)
        pumps.append(tcp)
        return tcp
    monkeypatch.setattr("backend.app.drivers.yoosee.p2p.account_read_worker.open_tcp_media", opener)
    def execute():
        return owner.run(sock, initial_media=entries[0].spec, video_delivery=entries[0].video,
            additional_media=entries[1:], with_media_tcp=True, media_policy=MediaPolicy.CONTINUOUS,
            cancelled=lambda: sock.now >= duration, wait=sock.wait)
    return sock, entries, events, pumps, execute


def test_worker_pumps_three_channels_without_a_second_broker_reader(monkeypatch):
    sock, entries, events, pumps, execute = run(monkeypatch)
    result = execute()
    assert result.reason == "stopped"
    assert len(pumps) == sock.opens == sock.closes == 3 and sock.closed == 1
    assert len(result.media_channels) == 3
    assert all(tcp.steps > 10 and tcp.finishes == 1 for tcp in pumps)
    assert max(tcp.steps for tcp in pumps) - min(tcp.steps for tcp in pumps) <= 3
    assert all(entry.video.closed for entry in entries)
    assert media_channels_closed(result.media_channels)
    assert result.media is result.media_channels[0].media
    assert result.tcp is result.media_channels[0].tcp
    # No send/reopen is attempted after teardown; all three TCPs finish once.
    assert sum(event[0] == "finish" for event in events) == 3


def test_one_tcp_cleanup_exception_still_closes_other_transports_and_routes(monkeypatch):
    sock, entries, _, pumps, execute = run(monkeypatch, fail=100)
    with pytest.raises(OSError, match="synthetic cleanup"):
        execute()
    assert len(pumps) == 3 and all(tcp.finishes == 1 for tcp in pumps)
    assert sock.closes == 3 and sock.closed == 1
    assert all(entry.video.closed for entry in entries)


def test_cleanly_retired_failed_channel_does_not_stop_healthy_siblings(monkeypatch):
    sock, entries, _, pumps, execute = run(monkeypatch, fail="step100")
    result = execute()
    assert result.reason == "stopped" and len(pumps) == 3
    assert pumps[0].steps == 3
    assert all(tcp.steps > 50 for tcp in pumps[1:])
    assert result.media_channels[0].failure == "socket"
    assert result.media_channels[0].media.broker_release
    assert not result.media_channels[0].tcp.close_acknowledged
    assert not media_channels_closed(result.media_channels)
    assert media_channels_closed(result.media_channels, require_tcp_receipt=False)
    assert all(entry.video.closed for entry in entries)
    assert sock.opens == sock.closes == 3 and sock.closed == 1


def test_failed_channel_without_broker_receipt_still_retires_the_group(monkeypatch):
    sock, entries, _, pumps, execute = run(monkeypatch, fail="step100", duration=2.0)
    sock.media_fault = "silent"
    result = execute()
    assert result.reason == "transport" and len(pumps) == 3
    assert not result.media_channels[0].media.broker_release
    assert not media_channels_closed(result.media_channels, require_tcp_receipt=False)
    assert sock.opens == sock.closes == 3 and sock.closed == 1
    assert all(entry.video.closed for entry in entries)


def test_lifecycle_blocks_reauthentication_when_only_sibling_cleanup_failed(monkeypatch):
    sock = MediaSocket()
    route = MediaRouteResult(True, True, True)
    tcp = TcpMediaResult(True, True, False, 1, 1, 1)
    channels = (MediaChannelSummary(100, route, tcp),
                MediaChannelSummary(101, replace(route, broker_release=False), tcp))
    summary = WorkerSummary("stopped", 0, 0, 0, False, media=route, tcp=tcp, media_channels=channels)
    def execute(self, transport, **kwargs):
        transport.close()
        return summary
    monkeypatch.setattr(AccountReadWorker, "run", execute)
    lifecycle = AccountReadLifecycle(clock=lambda: sock.now)
    assert lifecycle.run(lambda _: connection(sock)) is summary
    assert lifecycle.state == "blocked"
    with pytest.raises(ValueError):
        lifecycle.run(lambda _: pytest.fail("must not authenticate another generation"))


@pytest.mark.parametrize("bad", ["missing_route", "broker", "tcp_close", "tcp_receipt"])
def test_successful_primary_cannot_hide_a_failed_sibling(bad):
    route = MediaRouteResult(True, True, True)
    tcp = TcpMediaResult(True, True, False, 1, 1, 1)
    primary = MediaChannelSummary(100, route, tcp)
    sibling = MediaChannelSummary(101,
        None if bad == "missing_route" else replace(route, broker_release=bad != "broker"),
        replace(tcp, socket_close_failed=bad == "tcp_close", close_acknowledged=bad != "tcp_receipt"))
    channels = (primary, sibling)
    assert not media_channels_closed(channels)
    worker = WorkerSummary("socket", 0, 0, 0, False, media=route, tcp=tcp, media_channels=channels)
    # Physically closed, broker-released siblings may lack only the inner ACK.
    assert retired_media_route(worker) is (bad == "tcp_receipt")


@pytest.mark.parametrize("bad", ["capacity", "duplicate_delivery", "duplicate_device", "untyped"])
def test_rejected_bootstrap_never_sends_open(monkeypatch, bad):
    sock, entries, _, pumps, _ = run(monkeypatch)
    owner = AccountReadWorker(NODE, 1, "192.0.2.1", 1234, clock=lambda: sock.now,
                              policy=AccountPolicy.CONTINUOUS, media_capacity=1 if bad == "capacity" else 3)
    additional = entries[1:]
    if bad == "duplicate_delivery":
        additional = (replace(entries[1], video=entries[0].video),)
    if bad == "duplicate_device":
        additional = (replace(entries[1], spec=entries[0].spec),)
    if bad == "untyped":
        additional = [entries[1]]
    result = owner.run(sock, initial_media=entries[0].spec, video_delivery=entries[0].video,
        additional_media=additional, with_media_tcp=True, media_policy=MediaPolicy.CONTINUOUS,
        wait=sock.wait)
    assert result.reason == "protocol"
    assert not pumps and sock.opens == sock.closes == 0 and sock.closed == 1
