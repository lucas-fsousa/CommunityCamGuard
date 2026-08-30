"""Open one authenticated brokered control session to a durable Yoosee enrollment."""

from __future__ import annotations

import socket
import time

from ....db.p2p import P2PEnrollment
from .access_session import (
    establish_initialized_node,
    heartbeat_node,
    obtain_list,
)
from .contracts import CertifiedNode, LoginMaterial, OnlineDevice, P2PProbeError


def open_camera_session(
    sock: socket.socket,
    enrollment: P2PEnrollment,
    timeout: float,
    deadline: float,
) -> tuple[CertifiedNode, OnlineDevice, int]:
    """Open the access-node control route for exactly the enrolled camera.

    Thing-model, passthrough and resource-service messages already carry their destination device
    ID and are routed by the authenticated access node.  A4/CA/CB is a separate direct-media
    rendezvous and must not be opened for these brokered controls: doing so consumes a camera link
    slot that cannot be reclaimed by merely closing this UDP socket.
    """

    material = LoginMaterial(enrollment.access_id, enrollment.access_token)
    endpoints = obtain_list(sock, material.access_id, timeout, deadline=deadline)
    endpoints.sort(key=lambda endpoint: endpoint[1] != 19800)
    node, devices, _skipped = establish_initialized_node(
        sock,
        material,
        endpoints[:8],
        timeout,
        deadline=deadline,
    )
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise P2PProbeError("P2P camera session exhausted its time budget")
    node = heartbeat_node(sock, node, min(timeout, remaining))
    target = next(
        (device for device in devices if str(device.device_id) == enrollment.device_id),
        None,
    )
    if target is None or not target.status:
        raise P2PProbeError("selected P2P camera is not online")
    return node, target, node.next_sequence
