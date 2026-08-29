"""Typed legacy automatic/day/night image-mode control for supported Yoosee cameras.

The selected camera family exposes the proven scalar ``nightViewMode`` contract.  The unrelated
V2 bitfield is intentionally not accepted here because these cameras do not advertise it.
"""

from __future__ import annotations

import socket
import time
from dataclasses import dataclass

from ....db.p2p import P2PEnrollment
from .camera_session import open_camera_session
from .contracts import CertifiedNode, ModelWriteResult, OnlineDevice, P2PProbeError
from .model_session import exchange_model_read
from .model_write_protocol import build_model_write, parse_model_write_response
from .model_write_session import exchange_model_write

NIGHT_VISION_READ_PATH = "ProWritable.videoParm"
NIGHT_VISION_WRITE_PATH = "ProWritable.videoParm.setVal.nightViewMode"
NIGHT_VISION_VALUES = {"automatic": 0, "daytime": 1, "night": 2}


@dataclass(frozen=True, slots=True)
class P2PNightVisionWrite:
    device_id: str
    mode: str
    previous_value: int
    requested_value: int
    changed: bool
    transport_acknowledged: bool
    error_code: int | None
    verified: bool


def build_night_vision_write(
    node: CertifiedNode,
    device_id: int,
    mode: str,
    sequence: int,
    message_id: int,
) -> bytes:
    """Build only the recovered three-state legacy D2 write."""

    if mode not in NIGHT_VISION_VALUES:
        raise ValueError("night-vision mode must be automatic, daytime or night")
    return build_model_write(
        node,
        device_id,
        NIGHT_VISION_WRITE_PATH,
        NIGHT_VISION_VALUES[mode],
        sequence,
        message_id,
    )


def parse_night_vision_write_response(frame: bytes, message_id: int) -> int | None:
    """Return the D3 error code only for the matching application request."""

    return parse_model_write_response(frame, message_id)


def extract_night_vision_mode(value: object) -> int | None:
    """Extract only one proven legacy mode, never a V2 support bitfield."""

    if isinstance(value, int) and not isinstance(value, bool):
        return value if value in NIGHT_VISION_VALUES.values() else None
    if isinstance(value, dict):
        direct = value.get("nightViewMode")
        if isinstance(direct, int) and not isinstance(direct, bool):
            if direct in NIGHT_VISION_VALUES.values():
                return direct
        for key, nested in value.items():
            if key == "nightViewModeV2":
                continue
            candidate = extract_night_vision_mode(nested)
            if candidate is not None:
                return candidate
    return None


def exchange_night_vision_write(
    sock: socket.socket,
    node: CertifiedNode,
    device: OnlineDevice,
    mode: str,
    sequence: int,
    timeout: float,
    *,
    retries: int = 3,
    deadline: float | None = None,
) -> ModelWriteResult:
    """Send one typed legacy-mode write and wait for its matching D3 response."""

    if mode not in NIGHT_VISION_VALUES:
        raise ValueError("night-vision mode must be automatic, daytime or night")
    if retries < 1:
        raise ValueError("night-vision-write retries must be positive")
    return exchange_model_write(
        sock,
        node,
        device,
        NIGHT_VISION_WRITE_PATH,
        NIGHT_VISION_VALUES[mode],
        sequence,
        timeout,
        retries=retries,
        deadline=deadline,
    )


def set_camera_night_vision(
    enrollment: P2PEnrollment,
    mode: str,
    *,
    timeout: float = 1.5,
    total_timeout: float = 30.0,
) -> P2PNightVisionWrite:
    """Set a proven legacy mode with mandatory preflight and exact fresh readback."""

    if mode not in NIGHT_VISION_VALUES:
        raise ValueError("night-vision mode must be automatic, daytime or night")
    requested = NIGHT_VISION_VALUES[mode]
    bounded_timeout = max(0.5, min(float(timeout), 5.0))
    deadline = time.monotonic() + max(10.0, min(float(total_timeout), 40.0))
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("", 0))
    try:
        node, target, sequence = open_camera_session(sock, enrollment, bounded_timeout, deadline)
        preflight = exchange_model_read(
            sock,
            node,
            target,
            NIGHT_VISION_READ_PATH,
            sequence,
            min(5.0, max(0.5, deadline - time.monotonic())),
            deadline=deadline,
        )
        previous = extract_night_vision_mode(preflight.value)
        if preflight.error_code != 0 or previous is None:
            raise P2PProbeError("night-vision preflight returned no supported legacy state")
        if previous == requested:
            return P2PNightVisionWrite(
                enrollment.device_id, mode, previous, requested, False, False, 0, True
            )

        write = exchange_night_vision_write(
            sock,
            node,
            target,
            mode,
            (sequence + 1) & 0xFFFFFFFF,
            bounded_timeout,
            deadline=deadline,
        )
        if write.error_code != 0:
            raise P2PProbeError("camera rejected the night-vision change")

        verified = False
        for attempt in range(5):
            if attempt:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                time.sleep(min(0.5, remaining))
            readback = exchange_model_read(
                sock,
                node,
                target,
                NIGHT_VISION_READ_PATH,
                (sequence + 2 + attempt) & 0xFFFFFFFF,
                min(bounded_timeout, max(0.5, deadline - time.monotonic())),
                retries=1,
                deadline=deadline,
            )
            if readback.error_code == 0 and extract_night_vision_mode(readback.value) == requested:
                verified = True
                break
        if not verified:
            raise P2PProbeError("camera did not confirm the night-vision change")
    except P2PProbeError:
        raise
    except (OSError, ValueError) as exc:
        raise P2PProbeError("P2P night-vision change failed") from exc
    finally:
        sock.close()
    return P2PNightVisionWrite(
        enrollment.device_id,
        mode,
        previous,
        requested,
        True,
        write.transport_acknowledged,
        write.error_code,
        True,
    )
