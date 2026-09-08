"""Bounded read-only Yoosee IoTVideo onboard-recording list exchange."""

from __future__ import annotations

import secrets
import socket
import time
from dataclasses import dataclass

from ....db.p2p import P2PEnrollment
from ...contracts import OnboardRecordingQuery
from .camera_session import open_camera_session
from .contracts import CertifiedNode, OnlineDevice, P2PProbeError
from .onboard_playback_carrier import (
    build_onboard_playback_list_request,
    parse_onboard_playback_list_response,
)
from .onboard_playback_modern import ModernPlaybackPage
from .onboard_playback_protocol import select_onboard_playback_protocol
from .onboard_playback_transport import (
    exchange_built_in_read,
    require_runtime_playback_read_certified,
)
from .onboard_playback_v34 import (
    merge_modern_playback_v4_fragments,
)


@dataclass(frozen=True, slots=True)
class OnboardPlaybackListExchange:
    transport_acknowledged: bool
    application_acknowledged: bool
    page: ModernPlaybackPage | None


def exchange_onboard_playback_list(
    sock: socket.socket,
    node: CertifiedNode,
    access_id: int,
    device: OnlineDevice,
    query: OnboardRecordingQuery,
    sequence: int,
    timeout: float,
    *,
    page_index: int = 0,
    protocol_version: int = 2,
    device_platform_version: int | None = None,
    retries: int = 3,
    deadline: float | None = None,
) -> OnboardPlaybackListExchange:
    """Perform one idempotent command-16 list exchange without opening media playback."""

    require_runtime_playback_read_certified()
    protocol_version = select_onboard_playback_protocol(
        query,
        requested_version=protocol_version,
        minimum_version=2,
        device_platform_version=device_platform_version,
    )
    message_id = secrets.randbits(31)
    request_id = secrets.randbits(32)
    request = build_onboard_playback_list_request(
        node,
        access_id,
        device.device_id,
        query,
        sequence,
        message_id,
        request_id,
        page_index=page_index,
        protocol_version=protocol_version,
    )

    def response_set_complete(responses: tuple[ModernPlaybackPage, ...]) -> bool:
        if protocol_version != 4:
            return True
        fragment_count = responses[0].fragment_count
        return (
            fragment_count is not None
            and fragment_count <= 64
            and len({response.fragment_index for response in responses}) == fragment_count
        )

    raw = exchange_built_in_read(
        sock,
        node,
        request,
        message_id=message_id,
        sequence=sequence,
        timeout=timeout,
        parse_response=lambda frame: parse_onboard_playback_list_response(
            frame,
            message_id=message_id,
            protocol_version=protocol_version,
        ),
        response_set_complete=response_set_complete,
        retries=retries,
        deadline=deadline,
    )
    page = raw.responses[0] if protocol_version != 4 and raw.responses else None
    if protocol_version == 4 and raw.responses:
        unique = {response.fragment_index: response for response in raw.responses}
        try:
            page = merge_modern_playback_v4_fragments(tuple(unique.values()))
        except ValueError:
            page = None
    return OnboardPlaybackListExchange(
        raw.transport_acknowledged,
        raw.application_acknowledged,
        page,
    )


def list_camera_onboard_recordings(
    enrollment: P2PEnrollment,
    query: OnboardRecordingQuery,
    *,
    page_index: int = 0,
    protocol_version: int = 2,
    device_platform_version: int | None = None,
    timeout: float = 1.5,
    total_timeout: float = 25.0,
) -> OnboardPlaybackListExchange:
    """Open one bounded brokered session and list via an explicitly selected V1-V4 codec."""

    require_runtime_playback_read_certified()
    bounded_timeout = max(0.5, min(float(timeout), 5.0))
    deadline = time.monotonic() + max(8.0, min(float(total_timeout), 35.0))
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("", 0))
    try:
        node, target, sequence = open_camera_session(sock, enrollment, bounded_timeout, deadline)
        return exchange_onboard_playback_list(
            sock,
            node,
            enrollment.access_id,
            target,
            query,
            sequence,
            bounded_timeout,
            page_index=page_index,
            protocol_version=protocol_version,
            device_platform_version=device_platform_version,
            deadline=deadline,
        )
    except P2PProbeError:
        raise
    except (OSError, ValueError) as exc:
        raise P2PProbeError("P2P onboard playback listing failed") from exc
    finally:
        sock.close()
