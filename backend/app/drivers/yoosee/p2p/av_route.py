"""Experimental fresh-route AV probe; no production registration or automatic retry.

The caller must hold the application's camera reservation across this whole call.
Reviewed identifiers come from the operator's test target, never browser input.
"""

from __future__ import annotations

import logging
import math
import secrets
import socket
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import cast

from ....db.p2p import P2PEnrollment
from .av_meter import AvMeter
from .av_probe import AvProbeResult, probe_av_socket
from .av_route_io import BudgetSocket
from .av_sample import AvVideoSample
from .camera_session import open_camera_session
from .contracts import CallingAttempt, P2PProbeError
from .media_protocol import build_av_init
from .media_session import open_media_channel
from .rendezvous_session import call_device, close_device_route

log = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class AvRouteResult:
    media: AvProbeResult
    route_release_acknowledged: bool


class AvBootstrapError(P2PProbeError):
    """Safe bootstrap observations, not decrypted payload or network identity."""

    def __init__(self, *, direct_acknowledged: bool, meter_acknowledged: bool, datagrams: int,
                 meter_roundtrip_confirmed: bool = False,
                 meter_observations: tuple[str, ...] = ()):
        super().__init__("native AV media bootstrap incomplete")
        self.observations = dict(phase="media_meter", direct_acknowledged=direct_acknowledged,
                                 meter_acknowledged=meter_acknowledged, datagrams=datagrams,
                                 meter_roundtrip_confirmed=meter_roundtrip_confirmed,
                                 meter_observations=meter_observations)


def probe_av_route(enrollment: P2PEnrollment, *, camera_id: str, device_id: str,
                   duration: float = 3.0,
                   sample: AvVideoSample | None = None,
                   request_user_data: bytes | None = None,
                   cancelled: Callable[[], bool] = lambda: False) -> AvRouteResult:
    """Return a retained sample only after successful AV/B9/socket cleanup.

    Optional immutable startup metadata is internal, operator-reviewed input,
    never an HTTP argument. This primitive does not infer platform or grant HD
    support; the diagnostic policy must validate those before selecting a profile.
    """
    try:
        return _probe_av_route(enrollment, camera_id=camera_id, device_id=device_id,
                               duration=duration, sample=sample, cancelled=cancelled,
                               request_user_data=request_user_data)
    except BaseException:
        if sample is not None:
            sample.close()
        raise


def _probe_av_route(enrollment: P2PEnrollment, *, camera_id: str, device_id: str,
                   duration: float,
                   sample: AvVideoSample | None,
                   request_user_data: bytes | None = None,
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
    # Validate immutable live metadata before acquiring a socket or opening a route.
    connection_type = 1 if request_user_data is not None else None
    build_av_init(0, request_user_data=request_user_data, connection_type=connection_type)
    if cancelled():
        raise P2PProbeError("native AV probe cancelled")
    bounded = BudgetSocket(socket.socket(socket.AF_INET, socket.SOCK_DGRAM), cancelled)
    sock = cast(socket.socket, bounded)
    node = target = None
    attempt = None
    release_sequence = 0
    released = False
    release_attempted = False
    stage = "bind"
    outcome = "incomplete"
    failure_type = "none"
    started = time.monotonic()
    try:
        sock.bind(("", 0))
        stage = "access_session"
        node, target, _ = open_camera_session(sock, enrollment, 1.0, bounded.deadline)
        stage = "target_validation"
        if str(target.device_id) != device_id or not target.status:
            raise P2PProbeError("native AV target is not the reviewed online camera")
        attempt = CallingAttempt(secrets.randbelow(0xFFFFFF) + 1,
                                 secrets.randbits(32), secrets.token_bytes(8))
        # One A4 uses node.next_sequence; direct A4 uses next_sequence + 1.
        # Reserve a distinct B9 sequence even when either send fails ambiguously.
        release_sequence = (node.next_sequence + 2) & 0xFFFFFFFF
        stage = "rendezvous"
        calling = call_device(sock, node, enrollment.access_id, target, 1.0,
                              retries=1, deadline=bounded.deadline, attempt=attempt,
                              request_user_data=request_user_data, connection_type=connection_type)
        if not calling.direct_handshake:
            raise P2PProbeError("native AV direct route was not established")
        stage = "media_meter"
        channel = open_media_channel(sock, node, enrollment.access_id, target, calling, 0.5,
                                     require_roundtrip=True, request_user_data=request_user_data,
                                     connection_type=connection_type)
        if not channel.meter_roundtrip_confirmed:
            raise AvBootstrapError(direct_acknowledged=channel.direct_acknowledged,
                                    meter_acknowledged=channel.meter_acknowledged,
                                    datagrams=channel.datagrams,
                                    meter_roundtrip_confirmed=channel.meter_roundtrip_confirmed,
                                    meter_observations=channel.meter_observations)
        if calling.peer_endpoint is None:
            raise P2PProbeError("native AV route has no correlated endpoint")
        bounded.check()
        bounded.phase(duration + 2)
        stage = "av_receive_close"
        result = probe_av_socket(sock, calling, channel, duration=duration,
                                 cancelled=cancelled, close_socket=False, sample=sample,
                                 request_user_data=request_user_data,
                                 meter=AvMeter(calling.peer_endpoint, attempt.link_id,
                                               attempt.call_id, enrollment.access_id, target.device_id))
        stage = "route_release"
        outcome = "av_completed"
    except P2PProbeError as exc:
        outcome, failure_type = "failed", type(exc).__name__
        raise
    except (OSError, ValueError) as exc:
        outcome, failure_type = "failed", type(exc).__name__
        raise P2PProbeError("native AV route probe failed") from None
    finally:
        try:
            if node is not None and target is not None and attempt is not None:
                bounded.phase(1.0, cleanup=True)
                release_attempted = True
                try:
                    released = close_device_route(sock, node, enrollment.access_id, target,
                                                   attempt.link_id, release_sequence, 0.8,
                                                   require_correlated_ack=True)
                except (OSError, ValueError, P2PProbeError):
                    released = False
        finally:
            try:
                sock.close()
            finally:
                # No exception text, endpoint, camera/device ID, wire or credentials.
                # Log after cleanup, retaining the original failed stage.
                log.warning("native_av_probe stage=%s outcome=%s error_type=%s "
                            "release_attempted=%s release_acknowledged=%s elapsed_ms=%d",
                            stage, outcome, failure_type, release_attempted, released,
                            int((time.monotonic() - started) * 1000))
    if not released:
        raise P2PProbeError("native AV route release receipt not confirmed")
    return AvRouteResult(result, released)
