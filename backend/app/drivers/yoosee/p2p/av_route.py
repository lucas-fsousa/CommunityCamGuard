"""Experimental fresh-route AV probe; no production registration or automatic retry.

The caller must hold the application's camera reservation across this whole call.
Reviewed identifiers come from the operator's test target, never browser input.
"""

from __future__ import annotations

import math
import secrets
import socket
from collections.abc import Callable
from dataclasses import dataclass
from typing import cast

from ....db.p2p import P2PEnrollment
from .av_meter import AvMeter
from .av_probe import AvProbeResult, probe_av_socket
from .av_route_io import BudgetSocket
from .camera_session import open_camera_session
from .contracts import CallingAttempt, P2PProbeError
from .media_session import open_media_channel
from .rendezvous_session import call_device, close_device_route


@dataclass(frozen=True, slots=True)
class AvRouteResult:
    media: AvProbeResult
    route_release_acknowledged: bool


def probe_av_route(enrollment: P2PEnrollment, *, camera_id: str, device_id: str,
                   duration: float = 3.0,
                   cancelled: Callable[[], bool] = lambda: False) -> AvRouteResult:
    """One fresh route: <=20s preparation, <=12s AV and <=1s B9 cleanup.

    No credential refresh, reconnect, legacy AV initializer or microphone calls.
    Even ambiguous A4 failure triggers cleanup of this preallocated route ID.
    """
    if (not camera_id or not device_id or enrollment.camera_id != camera_id
            or enrollment.device_id != device_id):
        raise ValueError("native AV probe requires the reviewed enrolled camera")
    if type(duration) not in (int, float) or not math.isfinite(duration) or not 0 < duration <= 10:
        raise ValueError("invalid native AV probe duration")
    if cancelled():
        raise P2PProbeError("native AV probe cancelled")
    bounded = BudgetSocket(socket.socket(socket.AF_INET, socket.SOCK_DGRAM), cancelled)
    sock = cast(socket.socket, bounded)
    node = target = None
    attempt = None
    release_sequence = 0
    released = False
    try:
        sock.bind(("", 0))
        node, target, _ = open_camera_session(sock, enrollment, 1.0, bounded.deadline)
        if str(target.device_id) != device_id or not target.status:
            raise P2PProbeError("native AV target is not the reviewed online camera")
        attempt = CallingAttempt(secrets.randbelow(0xFFFFFF) + 1,
                                 secrets.randbits(32), secrets.token_bytes(8))
        # One A4 uses node.next_sequence; direct A4 uses next_sequence + 1.
        # Reserve a distinct B9 sequence even when either send fails ambiguously.
        release_sequence = (node.next_sequence + 2) & 0xFFFFFFFF
        calling = call_device(sock, node, enrollment.access_id, target, 1.0,
                              retries=1, deadline=bounded.deadline, attempt=attempt)
        if not calling.direct_handshake:
            raise P2PProbeError("native AV direct route was not established")
        channel = open_media_channel(sock, node, enrollment.access_id, target, calling, 0.5)
        if calling.peer_endpoint is None:
            raise P2PProbeError("native AV route has no correlated endpoint")
        bounded.check()
        bounded.phase(duration + 2)
        result = probe_av_socket(sock, calling, channel, duration=duration,
                                 cancelled=cancelled, close_socket=False,
                                 meter=AvMeter(calling.peer_endpoint, attempt.link_id,
                                               attempt.call_id, enrollment.access_id, target.device_id))
    except (OSError, ValueError):
        raise P2PProbeError("native AV route probe failed") from None
    finally:
        try:
            if node is not None and target is not None and attempt is not None:
                bounded.phase(1.0, cleanup=True)
                try:
                    released = close_device_route(sock, node, enrollment.access_id, target,
                                                   attempt.link_id, release_sequence, 0.8,
                                                   require_correlated_ack=True)
                except (OSError, ValueError, P2PProbeError):
                    released = False
        finally:
            sock.close()
    if not released:
        raise P2PProbeError("native AV route release receipt not confirmed")
    return AvRouteResult(result, released)
