"""Yoosee adapter for the vendor-neutral bounded audio-message contract."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ...db import p2p
from ..contracts import AudioMessageResult, ControlNotReady, ControlOperationError
from .p2p import P2PProbeError, send_pcm_intercom

if TYPE_CHECKING:
    from ...db.registry import Camera


def supported(camera: Camera) -> bool:
    return bool(camera.camera_id and p2p.has_enrollment_for_camera(camera.camera_id))


def send(camera: Camera, pcm16le: bytes) -> AudioMessageResult:
    if not camera.camera_id:
        raise ControlNotReady("camera has no stable public identity")
    enrollment = p2p.get_enrollment_for_camera(camera.camera_id)
    if enrollment is None:
        raise ControlNotReady("camera has no linked Yoosee P2P enrollment")
    try:
        result = send_pcm_intercom(enrollment, pcm16le)
    except (P2PProbeError, RuntimeError, ValueError) as exc:
        raise ControlOperationError(str(exc)) from exc
    audio = result.control.audio
    return AudioMessageResult(
        duration_ms=len(pcm16le) // 16,
        requested_frames=audio.requested_frames if audio else 0,
        sent_frames=audio.sent_frames if audio else 0,
        acknowledged_frames=audio.acknowledged_frames if audio else 0,
        direct_connection=result.direct_handshake,
        session_completed=result.control.completed,
        route_released=result.route_released,
    )
