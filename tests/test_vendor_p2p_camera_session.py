from __future__ import annotations

from backend.app.db.p2p import P2PEnrollment
from backend.app.drivers.yoosee.p2p import camera_session
from backend.app.drivers.yoosee.p2p.contracts import (
    CertifiedNode,
    OnlineDevice,
)


def test_camera_session_selects_only_the_durable_enrollment(monkeypatch):
    enrollment = P2PEnrollment("7000000002", 123, bytes(range(64)), None, "now", "now")
    node = CertifiedNode(("192.0.2.10", 19800), 9, bytes(32), 17)
    devices = (
        OnlineDevice(7000000001, 1, False, 1, bytes(16)),
        OnlineDevice(7000000002, 1, False, 1, bytes(16)),
    )
    monkeypatch.setattr(
        camera_session,
        "obtain_list",
        lambda *_args, **_kwargs: [("192.0.2.10", 19800)],
    )
    monkeypatch.setattr(
        camera_session,
        "establish_initialized_node",
        lambda *_args, **_kwargs: (node, devices, 0),
    )
    monkeypatch.setattr(camera_session, "heartbeat_node", lambda *_args: node)

    opened_node, target, sequence = camera_session.open_camera_session(
        object(),  # type: ignore[arg-type]
        enrollment,
        0.1,
        10**20,
    )

    assert opened_node == node
    assert target.device_id == 7000000002
    assert sequence == 17


def test_camera_control_session_never_opens_a_direct_media_route(monkeypatch):
    enrollment = P2PEnrollment("7000000002", 123, bytes(range(64)), None, "now", "now")
    node = CertifiedNode(("192.0.2.10", 19800), 9, bytes(32), 17)
    devices = (OnlineDevice(7000000002, 1, False, 1, bytes(16)),)

    monkeypatch.setattr(
        camera_session,
        "obtain_list",
        lambda *_args, **_kwargs: [("192.0.2.10", 19800)],
    )
    monkeypatch.setattr(
        camera_session,
        "establish_initialized_node",
        lambda *_args, **_kwargs: (node, devices, 0),
    )
    monkeypatch.setattr(camera_session, "heartbeat_node", lambda *_args: node)

    assert not hasattr(camera_session, "call_device")
    assert camera_session.open_camera_session(object(), enrollment, 0.1, 10**20) == (
        node,
        devices[0],
        node.next_sequence,
    )
