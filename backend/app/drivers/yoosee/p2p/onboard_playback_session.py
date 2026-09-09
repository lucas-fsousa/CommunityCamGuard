"""Fail-closed Yoosee onboard-recording list session boundary."""

from __future__ import annotations

import secrets
import socket
import time
from dataclasses import dataclass

from ....db.p2p import P2PEnrollment
from ...contracts import OnboardRecordingQuery
from .camera_session import open_camera_session
from .contracts import CertifiedNode, OnlineDevice
from .onboard_playback_carrier import (
    build_onboard_playback_carrier,
    build_onboard_playback_lan_carrier,
    build_onboard_playback_receipt,
    is_onboard_playback_peer_receipt,
    is_onboard_playback_transport_ack,
    unwrap_onboard_playback_carrier,
)
from .onboard_playback_message import build_onboard_playback_list_message
from .onboard_playback_modern import ModernPlaybackPage
from .onboard_playback_response import parse_onboard_playback_list_response
from .onboard_playback_transport import require_runtime_playback_read_certified
from .session_io import acknowledge_reliable_node_frame, decrypt_node_frame, receive_datagrams

SDK_DEFAULT_RESPONSE_TIMEOUT_SECONDS = 10.0


@dataclass(frozen=True, slots=True)
class OnboardPlaybackListExchange:
    transport_acknowledged: bool
    peer_receipt_acknowledged: bool
    page: ModernPlaybackPage | None


def _exchange_certified_onboard_playback_list(
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
    retries: int = 1,
    deadline: float | None = None,
    known_lan_copy: bool = False,
    count_per_page: int | None = None,
) -> OnboardPlaybackListExchange:
    """Perform one bounded, idempotent listing exchange after external certification."""

    if type(retries) is not int or not 1 <= retries <= 3:
        raise ValueError("onboard playback retries must be between 1 and 3")
    bounded_timeout = max(
        0.1,
        min(float(timeout), SDK_DEFAULT_RESPONSE_TIMEOUT_SECONDS),
    )
    request_id = secrets.randbits(32)
    message_id = secrets.randbelow(0x7FFFFFFF) + 1
    message = build_onboard_playback_list_message(
        query,
        request_id,
        page_index=page_index,
        protocol_version=protocol_version,
        count_per_page=count_per_page,
    )
    if known_lan_copy:
        request = build_onboard_playback_lan_carrier(
            access_id,
            device.device_id,
            sequence,
            message_id,
            message,
        )
    else:
        request = build_onboard_playback_carrier(
            node,
            access_id,
            device.device_id,
            sequence,
            message_id,
            message,
        )
    transport_acknowledged = False
    peer_receipt_acknowledged = False
    page = None
    absolute_deadline = (
        deadline if deadline is not None else time.monotonic() + retries * bounded_timeout
    )

    for retry in range(retries):
        if time.monotonic() >= absolute_deadline:
            break
        sock.sendto(request, node.address)
        receive_until = min(time.monotonic() + bounded_timeout, absolute_deadline)
        for wire, peer in receive_datagrams(sock, receive_until):
            if peer != node.address:
                continue
            plain = decrypt_node_frame(wire, node)
            if plain is None:
                continue
            if is_onboard_playback_transport_ack(plain, expected_sequence=sequence):
                transport_acknowledged = True
                continue
            if is_onboard_playback_peer_receipt(
                plain,
                expected_access_id=access_id,
                expected_device_id=device.device_id,
                expected_message_id=message_id,
            ):
                peer_receipt_acknowledged = True
                acknowledge_reliable_node_frame(sock, node, plain)
                continue
            try:
                response_message = unwrap_onboard_playback_carrier(
                    plain,
                    expected_access_id=access_id,
                    expected_device_id=device.device_id,
                )
                parsed_page = parse_onboard_playback_list_response(
                    response_message,
                    request_id,
                    protocol_version=protocol_version,
                )
            except ValueError:
                continue
            acknowledge_reliable_node_frame(sock, node, plain)
            sock.sendto(
                build_onboard_playback_receipt(
                    node,
                    plain,
                    (sequence + retry + 1) & 0xFFFFFFFF,
                    expected_access_id=access_id,
                    expected_device_id=device.device_id,
                ),
                node.address,
            )
            page = parsed_page
            break
        if page is not None:
            break
    return OnboardPlaybackListExchange(
        transport_acknowledged,
        peer_receipt_acknowledged,
        page,
    )


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
    """Run a listing exchange only after the production certification gate opens."""

    require_runtime_playback_read_certified()
    return _exchange_certified_onboard_playback_list(
        sock,
        node,
        access_id,
        device,
        query,
        sequence,
        timeout,
        page_index=page_index,
        protocol_version=protocol_version,
        retries=retries,
        deadline=deadline,
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
    """Open one bounded listing session only after production certification."""

    require_runtime_playback_read_certified()
    bounded_timeout = max(0.5, min(float(timeout), 5.0))
    deadline = time.monotonic() + max(5.0, min(float(total_timeout), 30.0))
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("", 0))
    try:
        node, target, sequence = open_camera_session(
            sock,
            enrollment,
            bounded_timeout,
            deadline,
        )
        return _exchange_certified_onboard_playback_list(
            sock,
            node,
            enrollment.access_id,
            target,
            query,
            sequence,
            SDK_DEFAULT_RESPONSE_TIMEOUT_SECONDS,
            page_index=page_index,
            protocol_version=protocol_version,
            deadline=deadline,
        )
    finally:
        sock.close()
