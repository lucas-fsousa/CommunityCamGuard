"""Fail-closed Yoosee onboard-recording date session boundary."""

from __future__ import annotations

import socket
from dataclasses import dataclass

from ....db.p2p import P2PEnrollment
from ...contracts import OnboardRecordingQuery
from .contracts import CertifiedNode, OnlineDevice
from .onboard_playback_dates import ModernPlaybackDatePage
from .onboard_playback_transport import require_runtime_playback_read_certified


@dataclass(frozen=True, slots=True)
class OnboardPlaybackDateExchange:
    transport_acknowledged: bool
    peer_receipt_acknowledged: bool
    page: ModernPlaybackDatePage | None


def exchange_onboard_playback_dates(
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
) -> OnboardPlaybackDateExchange:
    """Reject the unavailable live transport before touching the supplied socket."""

    require_runtime_playback_read_certified()

def list_camera_onboard_recording_dates(
    enrollment: P2PEnrollment,
    query: OnboardRecordingQuery,
    *,
    page_index: int = 0,
    protocol_version: int = 2,
    device_platform_version: int | None = None,
    timeout: float = 1.5,
    total_timeout: float = 25.0,
) -> OnboardPlaybackDateExchange:
    """Reject the unavailable live transport before opening a socket."""

    require_runtime_playback_read_certified()
