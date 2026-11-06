from dataclasses import replace

import pytest

from backend.app.drivers.yoosee.p2p.account_policy import AccountPolicy
from backend.app.drivers.yoosee.p2p.account_read_lifecycle import (
    AccountReadLifecycle,
    ReadConnection,
)
from backend.app.drivers.yoosee.p2p.media_lease_inbox import MediaLeaseInbox
from tests.test_broker_reads import NODE
from tests.test_media_lease_inbox import TARGETS, bootstrap, camera
from tests.test_media_lease_worker import setup


def test_sources_share_one_authenticated_generation_and_release_before_reuse(monkeypatch):
    sock, _, pumps, _ = setup(monkeypatch)
    lifecycle = AccountReadLifecycle(policy=AccountPolicy.CONTINUOUS, clock=lambda: sock.now)
    connections, boxes, tickets = [], [], []
    def connect(generation):
        connections.append(generation)
        return ReadConnection(NODE, sock, "192.0.2.1", 1234, TARGETS[0], 123, TARGETS)
    def leases(connection, generation):
        assert connection.access_id == 123 and lifecycle.state == "connecting"
        inbox = MediaLeaseInbox(generation, connection.access_id, targets=connection.targets)
        boxes.append(inbox)
        tickets.extend(inbox.submit(camera(index), bootstrap(index)) for index in range(2))
        return inbox
    result = lifecycle.run_media_leases(connect, leases=leases, cancelled=lambda: sock.now >= 0.1)
    assert result.reason == "stopped" and lifecycle.state == "idle"
    assert connections == [1] and len(pumps) == 2 and sock.closed == 1
    assert all(boxes[0].take(ticket).status == "closed" for ticket in tickets)
    with pytest.raises(ValueError):
        boxes[0].submit(camera(0), bootstrap(0))


@pytest.mark.parametrize("fault", ["generation", "account", "targets", "missing_identity"])
def test_mismatched_account_or_inventory_is_rejected_before_route_open(monkeypatch, fault):
    sock, _, pumps, _ = setup(monkeypatch)
    lifecycle = AccountReadLifecycle(policy=AccountPolicy.CONTINUOUS, clock=lambda: sock.now)
    connection = ReadConnection(NODE, sock, "192.0.2.1", 1234, TARGETS[0], 123, TARGETS)
    if fault == "missing_identity":
        connection = replace(connection, access_id=None)
    def leases(_, generation):
        return MediaLeaseInbox(generation + (fault == "generation"),
            999 if fault == "account" else 123,
            targets=TARGETS[:1] if fault == "targets" else TARGETS)
    with pytest.raises(ValueError, match="authenticated"):
        lifecycle.run_media_leases(lambda _: connection, leases=leases, cancelled=lambda: False)
    assert lifecycle.state == "blocked" and sock.closed == 1
    assert sock.opens == 0 and not pumps


def test_finite_lifecycle_never_authenticates_for_continuous_leases():
    lifecycle = AccountReadLifecycle()
    with pytest.raises(ValueError, match="continuous"):
        lifecycle.run_media_leases(lambda _: pytest.fail("no authentication"),
            leases=lambda *args: pytest.fail("no leases"), cancelled=lambda: False)
