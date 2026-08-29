"""Typed master switch for the selected camera family's smart-protection guard."""

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

SMART_PROTECTION_READ_PATH = "ProWritable.guardParm"
SMART_PROTECTION_WRITE_PATH = "ProWritable.guardParm.setVal.enable"


@dataclass(frozen=True, slots=True)
class P2PSmartProtectionState:
    device_id: str
    enabled: bool
    authenticated: bool
    direct_handshake: bool
    transport_acknowledged: bool
    error_code: int | None


@dataclass(frozen=True, slots=True)
class P2PSmartProtectionWrite:
    device_id: str
    enabled: bool
    previous_enabled: bool
    changed: bool
    transport_acknowledged: bool
    error_code: int | None
    verified: bool


def extract_smart_protection_enabled(value: object) -> bool | None:
    """Extract only ``guardParm.setVal.enable`` and its standalone scalar form."""

    if type(value) is int and value in (0, 1):
        return bool(value)
    if not isinstance(value, dict):
        return None
    direct = value.get("enable")
    if type(direct) is int and direct in (0, 1):
        return bool(direct)
    for key in ("setVal", "guardParm", "ProWritable"):
        if key in value:
            candidate = extract_smart_protection_enabled(value[key])
            if candidate is not None:
                return candidate
    return None


def build_smart_protection_write(
    node: CertifiedNode,
    device_id: int,
    enabled: bool,
    sequence: int,
    message_id: int,
) -> bytes:
    """Build only the recovered guard master-switch leaf write."""

    if type(enabled) is not bool:
        raise ValueError("smart-protection state must be a boolean")
    return build_model_write(
        node,
        device_id,
        SMART_PROTECTION_WRITE_PATH,
        int(enabled),
        sequence,
        message_id,
    )


def parse_smart_protection_write_response(frame: bytes, message_id: int) -> int | None:
    return parse_model_write_response(frame, message_id)


def exchange_smart_protection_write(
    sock: socket.socket,
    node: CertifiedNode,
    device: OnlineDevice,
    enabled: bool,
    sequence: int,
    timeout: float,
    *,
    retries: int = 3,
    deadline: float | None = None,
) -> ModelWriteResult:
    if type(enabled) is not bool:
        raise ValueError("smart-protection state must be a boolean")
    return exchange_model_write(
        sock,
        node,
        device,
        SMART_PROTECTION_WRITE_PATH,
        int(enabled),
        sequence,
        timeout,
        retries=retries,
        deadline=deadline,
    )


def read_camera_smart_protection(
    enrollment: P2PEnrollment,
    *,
    timeout: float = 1.5,
    total_timeout: float = 25.0,
) -> P2PSmartProtectionState:
    """Read the guard master switch on explicit request."""

    bounded_timeout = max(0.5, min(float(timeout), 5.0))
    deadline = time.monotonic() + max(8.0, min(float(total_timeout), 35.0))
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("", 0))
    try:
        node, target, sequence = open_camera_session(sock, enrollment, bounded_timeout, deadline)
        result = exchange_model_read(
            sock,
            node,
            target,
            SMART_PROTECTION_READ_PATH,
            sequence,
            min(5.0, max(0.5, deadline - time.monotonic())),
            deadline=deadline,
        )
        enabled = extract_smart_protection_enabled(result.value)
        if result.error_code != 0 or enabled is None:
            raise P2PProbeError("camera returned no supported smart-protection state")
    except P2PProbeError:
        raise
    except (OSError, ValueError) as exc:
        raise P2PProbeError("P2P smart-protection read failed") from exc
    finally:
        sock.close()
    return P2PSmartProtectionState(
        enrollment.device_id,
        enabled,
        True,
        True,
        result.transport_acknowledged,
        result.error_code,
    )


def set_camera_smart_protection(
    enrollment: P2PEnrollment,
    enabled: bool,
    *,
    timeout: float = 1.5,
    total_timeout: float = 30.0,
) -> P2PSmartProtectionWrite:
    """Set the guard master switch with preflight and exact fresh readback."""

    if type(enabled) is not bool:
        raise ValueError("smart-protection state must be a boolean")
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
            SMART_PROTECTION_READ_PATH,
            sequence,
            min(5.0, max(0.5, deadline - time.monotonic())),
            deadline=deadline,
        )
        previous = extract_smart_protection_enabled(preflight.value)
        if preflight.error_code != 0 or previous is None:
            raise P2PProbeError("smart-protection preflight returned no supported state")
        if previous is enabled:
            return P2PSmartProtectionWrite(
                enrollment.device_id, enabled, previous, False, False, 0, True
            )

        write = exchange_smart_protection_write(
            sock,
            node,
            target,
            enabled,
            (sequence + 1) & 0xFFFFFFFF,
            bounded_timeout,
            deadline=deadline,
        )
        if write.error_code != 0:
            raise P2PProbeError("camera rejected the smart-protection change")
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
                SMART_PROTECTION_READ_PATH,
                (sequence + 2 + attempt) & 0xFFFFFFFF,
                min(bounded_timeout, max(0.5, deadline - time.monotonic())),
                retries=1,
                deadline=deadline,
            )
            if (
                readback.error_code == 0
                and extract_smart_protection_enabled(readback.value) is enabled
            ):
                verified = True
                break
        if not verified:
            raise P2PProbeError("camera did not confirm the smart-protection change")
    except P2PProbeError:
        raise
    except (OSError, ValueError) as exc:
        raise P2PProbeError("P2P smart-protection change failed") from exc
    finally:
        sock.close()
    return P2PSmartProtectionWrite(
        enrollment.device_id,
        enabled,
        previous,
        True,
        write.transport_acknowledged,
        write.error_code,
        True,
    )
