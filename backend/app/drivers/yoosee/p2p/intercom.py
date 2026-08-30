"""High-level, device-scoped Yoosee intercom orchestration."""

from __future__ import annotations

import socket
import time
from dataclasses import dataclass

from ....db.p2p import P2PEnrollment
from .av_session import initialize_av_session
from .camera_session import open_camera_session
from .contracts import P2PProbeError
from .intercom_session import IntercomControlResult, run_silent_legacy_intercom_control
from .media_session import open_media_channel
from .rendezvous_session import call_device, close_device_route
from .renewal import run_with_fresh_access


@dataclass(frozen=True, slots=True)
class SilentIntercomProbeResult:
    device_id: str
    direct_handshake: bool
    media_meter_acknowledged: bool
    av_accepted: bool
    stream_version: int | None
    control: IntercomControlResult
    route_released: bool

    @property
    def completed(self) -> bool:
        return (
            self.direct_handshake
            and self.media_meter_acknowledged
            and self.av_accepted
            and self.control.completed
            and self.route_released
        )


def _empty_result(device_id: str) -> SilentIntercomProbeResult:
    return SilentIntercomProbeResult(
        device_id,
        False,
        False,
        False,
        None,
        IntercomControlResult(False, False, False, False, False),
        False,
    )


def _probe_silent_intercom(
    enrollment: P2PEnrollment,
    *,
    timeout: float,
    total_timeout: float,
) -> SilentIntercomProbeResult:
    bounded_timeout = max(0.5, min(float(timeout), 5.0))
    deadline = time.monotonic() + max(8.0, min(float(total_timeout), 45.0))
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("", 0))
    node = None
    target = None
    calling = None
    route_released = False
    result = _empty_result(enrollment.device_id)
    try:
        node, target, _sequence = open_camera_session(
            sock, enrollment, bounded_timeout, deadline
        )
        calling = call_device(
            sock,
            node,
            enrollment.access_id,
            target,
            min(bounded_timeout, max(0.1, deadline - time.monotonic())),
            deadline=deadline,
        )
        if calling.direct_handshake:
            media = open_media_channel(
                sock,
                node,
                enrollment.access_id,
                target,
                calling,
                min(bounded_timeout, max(0.1, deadline - time.monotonic())),
            )
            if media.meter_acknowledged:
                av = initialize_av_session(
                    sock,
                    calling,
                    min(bounded_timeout, max(0.1, deadline - time.monotonic())),
                )
                control = (
                    run_silent_legacy_intercom_control(
                        sock,
                        calling,
                        av,
                        min(bounded_timeout, max(0.1, deadline - time.monotonic())),
                    )
                    if av.accepted and av.stream_version == 1
                    else result.control
                )
                result = SilentIntercomProbeResult(
                    enrollment.device_id,
                    True,
                    True,
                    av.accepted,
                    av.stream_version,
                    control,
                    False,
                )
            else:
                result = SilentIntercomProbeResult(
                    enrollment.device_id,
                    True,
                    False,
                    False,
                    None,
                    result.control,
                    False,
                )
    except P2PProbeError:
        raise
    except (OSError, ValueError) as exc:
        raise P2PProbeError("silent P2P intercom probe failed") from exc
    finally:
        if node is not None and target is not None and calling is not None and calling.route_link_id:
            try:
                route_released = close_device_route(
                    sock,
                    node,
                    enrollment.access_id,
                    target,
                    calling.route_link_id,
                    (calling.next_sequence + 1) & 0xFFFFFFFF,
                    min(1.0, max(0.0, deadline - time.monotonic())),
                )
            except (OSError, ValueError):
                route_released = False
        sock.close()
    return SilentIntercomProbeResult(
        result.device_id,
        result.direct_handshake,
        result.media_meter_acknowledged,
        result.av_accepted,
        result.stream_version,
        result.control,
        route_released,
    )


def probe_silent_intercom(
    enrollment: P2PEnrollment,
    *,
    timeout: float = 1.5,
    total_timeout: float = 30.0,
) -> SilentIntercomProbeResult:
    """Validate the complete legacy control lifecycle while sending zero audio frames."""

    return run_with_fresh_access(
        enrollment,
        lambda current: _probe_silent_intercom(
            current,
            timeout=timeout,
            total_timeout=total_timeout,
        ),
    )
