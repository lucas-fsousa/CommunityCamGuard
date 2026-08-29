"""Bounded Yoosee deterrent pulse over the proven GDM action contract.

The module deliberately exposes no generic action sender. It can only invoke
``Action.expelCtrl.stVal`` with the recovered OFF/ON values. An ON request is never retried, every
attempt is followed by an explicit OFF in ``finally``, and the public pulse duration is capped.
"""

from __future__ import annotations

import secrets
import socket
import struct
import time
from dataclasses import dataclass

from ....db.p2p import P2PEnrollment
from .camera_session import open_camera_session
from .contracts import CertifiedNode, OnlineDevice, P2PProbeError
from .model_session import exchange_model_read
from .session_io import (
    acknowledge_reliable_node_frame,
    decrypt_node_frame,
    receive_datagrams,
)
from .wire import finish_mode2, new_header, randomized_flags

SIREN_READ_PATH = "Action.expelCtrl"
SIREN_ACTION_PATH = "Action.expelCtrl.stVal"
SIREN_OFF = 1
SIREN_ON = 2
MIN_PULSE_SECONDS = 1
MAX_PULSE_SECONDS = 10


@dataclass(frozen=True, slots=True)
class SirenActionExchange:
    transport_acknowledged: bool
    error_code: int | None


@dataclass(frozen=True, slots=True)
class P2PSirenPulse:
    device_id: str
    duration_seconds: int
    enable_transport_acknowledged: bool
    enable_error_code: int | None
    disable_transport_acknowledged: bool
    disable_error_code: int | None
    final_off_confirmed: bool


def build_siren_action(
    node: CertifiedNode,
    access_id: int,
    device_id: int,
    enabled: bool,
    sequence: int,
    message_id: int,
) -> bytes:
    """Build only the fixed target-type-3 expel ON/OFF action."""

    if type(enabled) is not bool:
        raise ValueError("siren state must be a boolean")
    encoded_path = SIREN_ACTION_PATH.encode("utf-8")
    encoded_json = str(SIREN_ON if enabled else SIREN_OFF).encode("ascii")
    length = 0x2A + 8 + len(encoded_path) + 1 + len(encoded_json) + 1
    frame = new_header(
        0xAC,
        length,
        node.session_id,
        sequence,
        randomized_flags(mode=2, proc=3),
    )
    frame[0] = 0x7E
    struct.pack_into("<Q", frame, 0x18, access_id)
    struct.pack_into("<I", frame, 0x20, message_id & 0x7FFFFFFF)
    struct.pack_into("<H", frame, 0x24, 1)
    frame[0x26] = 3
    frame[0x27] = len(encoded_path)
    struct.pack_into("<H", frame, 0x28, len(encoded_json))
    cursor = 0x2A
    struct.pack_into("<Q", frame, cursor, device_id)
    cursor += 8
    frame[cursor : cursor + len(encoded_path)] = encoded_path
    cursor += len(encoded_path) + 1
    frame[cursor : cursor + len(encoded_json)] = encoded_json
    return finish_mode2(frame, node.session_key)


def parse_siren_action_response(frame: bytes, message_id: int) -> int | None:
    """Return the AD error code only for the matching siren action message."""

    if len(frame) < 0x36 or frame[1] != 0xAD:
        return None
    if struct.unpack_from("<I", frame, 0x30)[0] != message_id:
        return None
    return struct.unpack_from("<H", frame, 0x34)[0]


def extract_siren_state(value: object) -> int | None:
    """Extract only the documented expel OFF/ON state from a model response."""

    if isinstance(value, int) and not isinstance(value, bool):
        return value if value in (SIREN_OFF, SIREN_ON) else None
    if isinstance(value, dict):
        state = value.get("stVal")
        if isinstance(state, int) and not isinstance(state, bool):
            if state in (SIREN_OFF, SIREN_ON):
                return state
        for nested in value.values():
            candidate = extract_siren_state(nested)
            if candidate is not None:
                return candidate
    return None


def exchange_siren_action(
    sock: socket.socket,
    node: CertifiedNode,
    access_id: int,
    device: OnlineDevice,
    enabled: bool,
    sequence: int,
    timeout: float,
    *,
    retries: int,
    deadline: float | None = None,
) -> SirenActionExchange:
    """Send one fixed siren action and await its correlated AD response."""

    if type(enabled) is not bool:
        raise ValueError("siren state must be a boolean")
    if retries < 1:
        raise ValueError("siren-action retries must be positive")
    message_id = secrets.randbits(31)
    request = build_siren_action(
        node,
        access_id,
        device.device_id,
        enabled,
        sequence,
        message_id,
    )
    transport_acknowledged = False
    error_code = None
    for _attempt in range(retries):
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
                if plain[1] == 0xAC:
                    transport_acknowledged = True
                continue
            candidate = parse_siren_action_response(plain, message_id)
            acknowledge_reliable_node_frame(sock, node, plain)
            if candidate is not None:
                error_code = candidate
                break
        if error_code is not None:
            break
    return SirenActionExchange(transport_acknowledged, error_code)


def _confirm_off(
    sock: socket.socket,
    node: CertifiedNode,
    device: OnlineDevice,
    sequence: int,
    timeout: float,
    deadline: float,
) -> bool:
    for attempt in range(5):
        if attempt:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            time.sleep(min(0.5, remaining))
        result = exchange_model_read(
            sock,
            node,
            device,
            SIREN_READ_PATH,
            (sequence + attempt) & 0xFFFFFFFF,
            min(timeout, max(0.5, deadline - time.monotonic())),
            retries=1,
            deadline=deadline,
        )
        if result.error_code == 0 and extract_siren_state(result.value) == SIREN_OFF:
            return True
    return False


def pulse_camera_siren(
    enrollment: P2PEnrollment,
    duration_seconds: int,
    *,
    timeout: float = 1.5,
    total_timeout: float = 35.0,
) -> P2PSirenPulse:
    """Emit one bounded pulse after an OFF preflight and always send an explicit OFF afterward."""

    if (
        type(duration_seconds) is not int
        or not MIN_PULSE_SECONDS <= duration_seconds <= MAX_PULSE_SECONDS
    ):
        raise ValueError(f"siren pulse must be {MIN_PULSE_SECONDS} to {MAX_PULSE_SECONDS} seconds")
    bounded_timeout = max(0.5, min(float(timeout), 5.0))
    deadline = time.monotonic() + max(20.0, min(float(total_timeout), 45.0))
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("", 0))
    try:
        node, target, sequence = open_camera_session(sock, enrollment, bounded_timeout, deadline)
        preflight = exchange_model_read(
            sock,
            node,
            target,
            SIREN_READ_PATH,
            sequence,
            min(5.0, max(0.5, deadline - time.monotonic())),
            retries=1,
            deadline=deadline,
        )
        if preflight.error_code != 0 or extract_siren_state(preflight.value) != SIREN_OFF:
            raise P2PProbeError("siren pulse requires a confirmed OFF preflight")
        if deadline - time.monotonic() < duration_seconds + 6.0:
            raise P2PProbeError("siren pulse has insufficient time budget for guaranteed cleanup")

        enabled: SirenActionExchange | None = None
        disabled: SirenActionExchange | None = None
        final_off_confirmed = False
        try:
            enabled = exchange_siren_action(
                sock,
                node,
                enrollment.access_id,
                target,
                True,
                (sequence + 0x10) & 0xFFFFFFFF,
                bounded_timeout,
                retries=1,
                deadline=deadline,
            )
            if enabled.error_code != 0:
                raise P2PProbeError("camera did not accept the siren activation")
            time.sleep(duration_seconds)
        finally:
            # Safety cleanup gets its own bounded budget. The original request deadline may expire
            # immediately after ON/sleep; it must never suppress the first explicit OFF attempt.
            cleanup_deadline = max(deadline, time.monotonic() + 8.0)
            disabled = exchange_siren_action(
                sock,
                node,
                enrollment.access_id,
                target,
                False,
                (sequence + 0x20) & 0xFFFFFFFF,
                bounded_timeout,
                retries=3,
                deadline=cleanup_deadline,
            )
            final_off_confirmed = _confirm_off(
                sock,
                node,
                target,
                (sequence + 0x30) & 0xFFFFFFFF,
                bounded_timeout,
                cleanup_deadline,
            )
        if disabled.error_code != 0 or not final_off_confirmed:
            raise P2PProbeError("camera did not confirm the final siren OFF state")
    except P2PProbeError:
        raise
    except (OSError, ValueError) as exc:
        raise P2PProbeError("P2P siren pulse failed") from exc
    finally:
        sock.close()
    assert enabled is not None and disabled is not None
    return P2PSirenPulse(
        device_id=enrollment.device_id,
        duration_seconds=duration_seconds,
        enable_transport_acknowledged=enabled.transport_acknowledged,
        enable_error_code=enabled.error_code,
        disable_transport_acknowledged=disabled.transport_acknowledged,
        disable_error_code=disabled.error_code,
        final_off_confirmed=final_off_confirmed,
    )
