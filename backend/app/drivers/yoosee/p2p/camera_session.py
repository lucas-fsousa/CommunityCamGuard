"""Open one authenticated direct session to a durable Yoosee enrollment."""

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
from .rendezvous_session import call_device


def open_camera_session(
    sock: socket.socket,
    enrollment: P2PEnrollment,
    timeout: float,
    deadline: float,
) -> tuple[CertifiedNode, OnlineDevice, int]:
    """Open one initialized route to exactly the durable enrollment's camera."""

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
    calling = call_device(
        sock,
        node,
        material.access_id,
        target,
        timeout,
        deadline=deadline,
    )
    if not calling.direct_handshake:
        raise P2PProbeError("selected P2P camera did not complete the direct handshake")
    return node, target, calling.next_sequence
