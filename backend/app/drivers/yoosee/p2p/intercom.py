"""High-level, device-scoped Yoosee intercom orchestration."""

from __future__ import annotations

import socket
import time

from ....db.p2p import P2PEnrollment
from .amr_nb import encode_pcm16le
from .av_session import initialize_av_session
from .camera_session import open_camera_session
from .contracts import P2PProbeError
from .intercom_result import IntercomProbeResult, empty_intercom_result
from .intercom_session import (
    run_legacy_intercom_control,
    run_silent_legacy_intercom_control,
)
from .media_session import open_media_channel
from .modern_intercom_session import run_modern_intercom_control
from .player_family import PlayerFamily, player_family
from .rendezvous_session import call_device, close_device_route
from .renewal import run_with_fresh_access

SilentIntercomProbeResult = IntercomProbeResult


def _empty_result(device_id: str) -> IntercomProbeResult:
    """Compatibility alias for existing internal callers and tests."""

    return empty_intercom_result(device_id)


def _probe_silent_intercom(
    enrollment: P2PEnrollment,
    *,
    timeout: float,
    total_timeout: float,
) -> IntercomProbeResult:
    return _probe_intercom(
        enrollment,
        timeout=timeout,
        total_timeout=total_timeout,
        audio_frames=(),
        failure_message="silent P2P intercom probe failed",
    )


def _probe_intercom(
    enrollment: P2PEnrollment,
    *,
    timeout: float,
    total_timeout: float,
    audio_frames: tuple[bytes, ...],
    failure_message: str,
) -> IntercomProbeResult:
    """Run one family-selected encoded session and always release its direct route."""

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
        node, target, _sequence = open_camera_session(sock, enrollment, bounded_timeout, deadline)
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
                if av.accepted and av.stream_version == 1:
                    control_timeout = min(bounded_timeout, max(0.1, deadline - time.monotonic()))
                    if player_family(enrollment.device_id) is PlayerFamily.IOTVIDEO:
                        control = run_modern_intercom_control(
                            sock,
                            calling,
                            av,
                            control_timeout,
                            audio_frames=audio_frames,
                        )
                    else:
                        control = (
                            run_legacy_intercom_control(
                                sock,
                                calling,
                                av,
                                control_timeout,
                                audio_frames=audio_frames,
                            )
                            if audio_frames
                            else run_silent_legacy_intercom_control(
                                sock,
                                calling,
                                av,
                                control_timeout,
                            )
                        )
                else:
                    control = result.control
                result = IntercomProbeResult(
                    enrollment.device_id,
                    True,
                    True,
                    av.accepted,
                    av.stream_version,
                    control,
                    False,
                )
            else:
                result = IntercomProbeResult(
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
        raise P2PProbeError(failure_message) from exc
    finally:
        if (
            node is not None
            and target is not None
            and calling is not None
            and calling.route_link_id
        ):
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
    return IntercomProbeResult(
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
) -> IntercomProbeResult:
    """Validate the selected control lifecycle while sending zero audio frames."""

    return run_with_fresh_access(
        enrollment,
        lambda current: _probe_silent_intercom(
            current,
            timeout=timeout,
            total_timeout=total_timeout,
        ),
    )


def send_pcm_intercom(
    enrollment: P2PEnrollment,
    pcm16le: bytes,
    *,
    timeout: float = 1.5,
    total_timeout: float = 45.0,
    max_seconds: float = 10.0,
) -> IntercomProbeResult:
    """Send bounded 8 kHz mono PCM through the selected native talk path.

    This function intentionally has no driver-control, HTTP or browser binding.
    """

    frames = encode_pcm16le(pcm16le, max_seconds=max_seconds)
    return run_with_fresh_access(
        enrollment,
        lambda current: _probe_intercom(
            current,
            timeout=timeout,
            total_timeout=total_timeout,
            audio_frames=frames,
            failure_message="P2P audio intercom operation failed",
        ),
    )
