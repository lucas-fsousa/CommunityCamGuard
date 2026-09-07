"""Bounded read-only Yoosee IoTVideo onboard-recording list exchange."""

from __future__ import annotations

import secrets
import socket
import struct
import time
from dataclasses import dataclass

from ....db.p2p import P2PEnrollment
from ...contracts import OnboardRecordingQuery
from .camera_session import open_camera_session
from .contracts import CertifiedNode, OnlineDevice, P2PProbeError
from .onboard_playback_carrier import (
    build_onboard_playback_list_request,
    build_onboard_playback_receipt,
    parse_onboard_playback_list_response,
)
from .onboard_playback_modern import ModernPlaybackPage
from .onboard_playback_v34 import (
    merge_modern_playback_v4_fragments,
)
from .session_io import acknowledge_reliable_node_frame, decrypt_node_frame, receive_datagrams


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
    retries: int = 3,
    deadline: float | None = None,
) -> OnboardPlaybackListExchange:
    """Perform one idempotent command-16 list exchange without opening media playback."""

    if retries < 1:
        raise ValueError("onboard playback-list retries must be positive")
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
    transport_acknowledged = False
    application_acknowledged = False
    page = None
    v4_fragments: dict[int, ModernPlaybackPage] = {}
    for retry in range(retries):
        if deadline is not None and time.monotonic() >= deadline:
            break
        sock.sendto(request, node.address)
        receive_until = time.monotonic() + timeout
        if deadline is not None:
            receive_until = min(receive_until, deadline)
        for wire, peer in receive_datagrams(sock, receive_until):
            if peer != node.address:
                continue
            plain = decrypt_node_frame(wire, node)
            if plain is None:
                continue
            flags = struct.unpack_from("<I", plain, 0x14)[0]
            if flags & (1 << 20):
                if plain[1] == 0xB9:
                    transport_acknowledged = True
                elif plain[1] == 0xBA:
                    application_acknowledged = True
                continue
            if plain[1] == 0xBA and len(plain) >= 0x34:
                if struct.unpack_from("<I", plain, 0x2C)[0] == message_id:
                    application_acknowledged = True
                    acknowledge_reliable_node_frame(sock, node, plain)
                continue
            parsed_page = parse_onboard_playback_list_response(
                plain,
                request_id=request_id,
                protocol_version=protocol_version,
            )
            if parsed_page is None:
                continue
            acknowledge_reliable_node_frame(sock, node, plain)
            sock.sendto(
                build_onboard_playback_receipt(
                    node,
                    plain,
                    (sequence + retry + 1) & 0xFFFFFFFF,
                ),
                node.address,
            )
            if protocol_version != 4:
                page = parsed_page
                break
            fragment_index = parsed_page.fragment_index
            fragment_count = parsed_page.fragment_count
            if fragment_index is None or fragment_count is None or fragment_count > 64:
                continue
            if v4_fragments and any(
                fragment.fragment_count != fragment_count
                or fragment.page_index != parsed_page.page_index
                or fragment.total_pages != parsed_page.total_pages
                for fragment in v4_fragments.values()
            ):
                v4_fragments.clear()
            v4_fragments.setdefault(fragment_index, parsed_page)
            if len(v4_fragments) == fragment_count:
                try:
                    page = merge_modern_playback_v4_fragments(tuple(v4_fragments.values()))
                except ValueError:
                    v4_fragments.clear()
                    continue
                break
        if page is not None:
            break
    return OnboardPlaybackListExchange(
        transport_acknowledged,
        application_acknowledged,
        page,
    )


def list_camera_onboard_recordings(
    enrollment: P2PEnrollment,
    query: OnboardRecordingQuery,
    *,
    page_index: int = 0,
    protocol_version: int = 2,
    timeout: float = 1.5,
    total_timeout: float = 25.0,
) -> OnboardPlaybackListExchange:
    """Open one bounded brokered session and list via an explicitly selected V1-V4 codec."""

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
            deadline=deadline,
        )
    except P2PProbeError:
        raise
    except (OSError, ValueError) as exc:
        raise P2PProbeError("P2P onboard playback listing failed") from exc
    finally:
        sock.close()
