"""Typed normal/180-degree image orientation control.

The module deliberately owns the complete orientation write surface.  It accepts only the two
proven states, performs a read preflight, sends the fixed D2 property and requires fresh readback.
No generic thing-model writer is exposed.
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

ORIENTATION_PATH = "ProWritable.videoParm.setVal.multiFlip"
ORIENTATION_READ_PATH = "ProWritable.videoParm"
ORIENTATION_VALUES = {"normal": 1, "inverted": 3}


@dataclass(frozen=True, slots=True)
class P2POrientationWrite:
    device_id: str
    orientation: str
    previous_value: int
    requested_value: int
    changed: bool
    transport_acknowledged: bool
    error_code: int | None
    verified: bool


def build_orientation_write(
    node: CertifiedNode,
    device_id: int,
    orientation: str,
    sequence: int,
    message_id: int,
) -> bytes:
    """Build only the proven normal/180-degree D2 property write."""

    if orientation not in ORIENTATION_VALUES:
        raise ValueError("orientation must be normal or inverted")
    return build_model_write(
        node,
        device_id,
        ORIENTATION_PATH,
        ORIENTATION_VALUES[orientation],
        sequence,
        message_id,
    )


def parse_orientation_write_response(frame: bytes, message_id: int) -> int | None:
    """Return the D3 error code only when it matches this orientation request."""

    return parse_model_write_response(frame, message_id)


def extract_orientation(value: object) -> int | None:
    """Extract only the selected model's documented normal/180-degree values."""

    if isinstance(value, int) and not isinstance(value, bool):
        return value if value in ORIENTATION_VALUES.values() else None
    if isinstance(value, dict):
        direct = value.get("multiFlip")
        if isinstance(direct, int) and not isinstance(direct, bool):
            if direct in ORIENTATION_VALUES.values():
                return direct
        for nested in value.values():
            candidate = extract_orientation(nested)
            if candidate is not None:
                return candidate
    return None


def exchange_orientation_write(
    sock: socket.socket,
    node: CertifiedNode,
    device: OnlineDevice,
    orientation: str,
    sequence: int,
    timeout: float,
    *,
    retries: int = 3,
    deadline: float | None = None,
) -> ModelWriteResult:
    """Send one typed D2 orientation write and wait for its matching D3 response."""

    if orientation not in ORIENTATION_VALUES:
        raise ValueError("orientation must be normal or inverted")
    if retries < 1:
        raise ValueError("orientation-write retries must be positive")
    return exchange_model_write(
        sock,
        node,
        device,
        ORIENTATION_PATH,
        ORIENTATION_VALUES[orientation],
        sequence,
        timeout,
        retries=retries,
        deadline=deadline,
    )


def set_camera_orientation(
    enrollment: P2PEnrollment,
    orientation: str,
    *,
    timeout: float = 1.5,
    total_timeout: float = 30.0,
) -> P2POrientationWrite:
    """Set normal/180-degree orientation with mandatory preflight and bounded readback."""

    if orientation not in ORIENTATION_VALUES:
        raise ValueError("orientation must be normal or inverted")
    requested = ORIENTATION_VALUES[orientation]
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
            ORIENTATION_READ_PATH,
            sequence,
            min(5.0, max(0.5, deadline - time.monotonic())),
            deadline=deadline,
        )
        previous = extract_orientation(preflight.value)
        if preflight.error_code != 0 or previous is None:
            raise P2PProbeError("camera orientation preflight returned no supported state")
        if previous == requested:
            return P2POrientationWrite(
                enrollment.device_id, orientation, previous, requested, False, False, 0, True
            )

        write = exchange_orientation_write(
            sock,
            node,
            target,
            orientation,
            (sequence + 1) & 0xFFFFFFFF,
            min(5.0, max(0.5, deadline - time.monotonic())),
            deadline=deadline,
        )
        if write.error_code != 0:
            raise P2PProbeError("camera rejected the orientation change")

        verified = False
        for attempt in range(5):
            if attempt:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                time.sleep(min(0.4, remaining))
            readback = exchange_model_read(
                sock,
                node,
                target,
                ORIENTATION_READ_PATH,
                (sequence + 2 + attempt) & 0xFFFFFFFF,
                min(2.0, max(0.5, deadline - time.monotonic())),
                retries=1,
                deadline=deadline,
            )
            if readback.error_code == 0 and extract_orientation(readback.value) == requested:
                verified = True
                break
        if not verified:
            raise P2PProbeError("camera did not confirm the orientation change")
    except P2PProbeError:
        raise
    except (OSError, ValueError) as exc:
        raise P2PProbeError("P2P orientation change failed") from exc
    finally:
        sock.close()
    return P2POrientationWrite(
        device_id=enrollment.device_id,
        orientation=orientation,
        previous_value=previous,
        requested_value=requested,
        changed=True,
        transport_acknowledged=write.transport_acknowledged,
        error_code=write.error_code,
        verified=True,
    )
