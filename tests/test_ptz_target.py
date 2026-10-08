"""Account socket sharing must never share a camera's capability authority."""
from dataclasses import replace

import pytest

from backend.app.drivers.yoosee.p2p import ptz_target
from backend.app.drivers.yoosee.p2p.contracts import OnlineDevice, P2PProbeError
from backend.app.drivers.yoosee.p2p.ptz_route import NativePtzRoute
from tests.test_ptz_prepare import ENTRY, IDENTITY
from tests.test_ptz_protocol import NODE
from tests.test_ptz_route import Socket

REVIEWED = frozenset({"right", "up"})
OTHER = replace(ENTRY, device_id="654321", camera_id="cam_other", dev_token="other")
OTHER_IDENTITY = replace(IDENTITY, device_id=OTHER.device_id)


def key(entry=ENTRY, identity=IDENTITY):
    return ((entry.camera_id, entry.device_id), identity, REVIEWED, entry.access_id, entry.access_token, entry.dev_token)


@pytest.fixture
def route():
    sock = Socket()
    result = NativePtzRoute(sock, NODE, access_id=ENTRY.access_id, device_id=int(ENTRY.device_id),
                            direction="right", sequence=20, allowed_directions=REVIEWED)
    result._inventory = {int(e.device_id): OnlineDevice(int(e.device_id), 1, False, 0, b"")
                         for e in (ENTRY, OTHER)}
    result._verified = {key(): REVIEWED}
    return result


def select(route, entry=ENTRY, identity=IDENTITY, direction="right"):
    return ptz_target.select_target(route, entry, identity, camera_id=entry.camera_id,
                                    direction=direction, reviewed=REVIEWED)


def test_camera_switch_reads_its_own_identity_then_reuses_known_target(route, monkeypatch):
    calls = []
    def verify(sock, node, target, enrollment, expected, direction, reviewed, sequence, deadline):
        calls.append((target.device_id, enrollment, expected, sequence))
        return frozenset({"right"})
    monkeypatch.setattr(ptz_target, "verify_ptz_target", verify)
    other = select(route, OTHER, OTHER_IDENTITY)
    assert calls == [(654321, OTHER, OTHER_IDENTITY, 22)]
    assert other._device_id == 654321 and other._allowed_directions == frozenset({"right"})
    assert route._closed and not other._closed
    original = select(other)
    assert len(calls) == 1 and original._device_id == 123456
    assert original._allowed_directions == REVIEWED
    assert original._receipt_sequence == 29
    assert not original._sock.sent
    route.close()
    other.close()
    assert original._sock.closed == 0
    original.close()
    assert original._sock.closed == 1


@pytest.mark.parametrize("field,value", [("access_id", 999), ("access_token", b"other"),
                                         ("device_id", "999"), ("camera_id", "cam_changed")])
def test_wrong_binding_cannot_reuse_camera_evidence(route, monkeypatch, field, value):
    monkeypatch.setattr(ptz_target, "verify_ptz_target", lambda *a: (_ for _ in ()).throw(P2PProbeError("reject")))
    with pytest.raises(P2PProbeError):
        select(route, replace(ENTRY, **{field: value}))
    assert not route._closed and not route._sock.sent


def test_same_model_does_not_skip_second_cameras_validation(route, monkeypatch):
    def reject(*args):
        raise P2PProbeError("unsupported model")
    monkeypatch.setattr(ptz_target, "verify_ptz_target", reject)
    with pytest.raises(P2PProbeError):
        select(route, OTHER, OTHER_IDENTITY)
    assert key(OTHER, OTHER_IDENTITY) not in route._verified
    assert route._receipt_sequence == 25  # allocated even when read result is uncertain
    assert not route._sock.sent


def test_changed_native_binding_cannot_reuse_same_public_cameras_capabilities(route, monkeypatch):
    route._verified = {key(identity=None): REVIEWED}
    seen = []
    def reject(*args):
        seen.append(args[2].device_id)
        raise P2PProbeError("new target requires independent validation")
    monkeypatch.setattr(ptz_target, "verify_ptz_target", reject)
    with pytest.raises(P2PProbeError):
        select(route, replace(ENTRY, device_id=OTHER.device_id), identity=None)
    assert seen == [654321]


def test_unknown_offline_or_unverified_axis_never_constructs_transfer(route, monkeypatch):
    route._inventory[654321] = replace(route._inventory[654321], status=0)
    with pytest.raises(P2PProbeError, match="not online"):
        select(route, OTHER, OTHER_IDENTITY)
    route._verified[key()] = frozenset({"right"})
    with pytest.raises(P2PProbeError, match="axis"):
        select(route, direction="up")
    route.send_start()
    with pytest.raises(P2PProbeError, match="uncertain"):
        select(route)
