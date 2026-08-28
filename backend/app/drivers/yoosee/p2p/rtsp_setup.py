"""Typed RTSP activation and credential delivery for the recovered Yoosee contract.

Only two writes exist here: the fixed ``ProWritable.onvifEn.setVal`` boolean property and
penetrate ``type=3`` carrying the camera's HA1.  An acknowledgement proves delivery only; LAN
media verification and registry persistence belong to the onboarding orchestrator.
"""

from __future__ import annotations

import hashlib
import json
import secrets
import socket
import string
import struct
import time
from dataclasses import dataclass

from ....db.p2p import P2PEnrollment
from . import client as transport

ONVIF_READ_PATH = "ProWritable.onvifEn"
ONVIF_WRITE_PATH = "ProWritable.onvifEn.setVal"


@dataclass(frozen=True, slots=True)
class P2PRtspEnableWrite:
    device_id: str
    enabled: bool
    previous_enabled: bool
    changed: bool
    transport_acknowledged: bool
    error_code: int | None
    verified: bool


@dataclass(frozen=True, slots=True)
class P2PRtspPreparation:
    device_id: str
    previous_enabled: bool
    enabled_changed: bool
    password_delivery_acknowledged: bool
    password_response_accepted: bool


@dataclass(frozen=True, slots=True)
class _PasswordExchange:
    transport_acknowledged: bool
    application_acknowledged: bool
    response: dict[str, object] | None


def generate_rtsp_password(length: int = 16) -> str:
    """Generate an APK-valid alphanumeric password without exposing it to the browser."""

    if not 8 <= int(length) <= 30:
        raise ValueError("RTSP password length must be between 8 and 30 characters")
    alphabet = string.ascii_letters + string.digits
    characters = [
        secrets.choice(string.ascii_lowercase),
        secrets.choice(string.ascii_uppercase),
        secrets.choice(string.digits),
        *(secrets.choice(alphabet) for _ in range(int(length) - 3)),
    ]
    secrets.SystemRandom().shuffle(characters)
    return "".join(characters)


def rtsp_password_digest(password: str) -> str:
    """Return the exact lowercase HA1 expected by the camera's ``type=3`` command."""

    if (
        not isinstance(password, str)
        or not 8 <= len(password) <= 30
        or not password.isalnum()
    ):
        raise ValueError("RTSP password must contain 8 to 30 alphanumeric characters")
    clear = f"admin:HIipCamera:{password}".encode()
    return hashlib.md5(clear, usedforsecurity=False).hexdigest()


def extract_onvif_enabled(value: object) -> bool | None:
    """Extract a boolean ONVIF/RTSP state from a leaf or nested thing-model response."""

    if type(value) is int and value in (0, 1):
        return bool(value)
    if isinstance(value, dict):
        for key in ("setVal", "value", "v"):
            candidate = value.get(key)
            if type(candidate) is int and candidate in (0, 1):
                return bool(candidate)
        for key, nested in value.items():
            if key in {"t", "time", "timestamp"}:
                continue
            candidate = extract_onvif_enabled(nested)
            if candidate is not None:
                return candidate
    return None


def build_onvif_enable_write(
    node: transport.CertifiedNode,
    device_id: int,
    enabled: bool,
    sequence: int,
    message_id: int,
) -> bytes:
    """Build only the fixed target-type-7 ``onvifEn`` boolean write."""

    if type(enabled) is not bool:
        raise ValueError("RTSP enabled state must be a boolean")
    encoded_path = ONVIF_WRITE_PATH.encode()
    encoded_json = str(int(enabled)).encode()
    length = 0x2A + 8 + len(encoded_path) + 1 + len(encoded_json) + 1
    frame = transport._new_header(
        0xD2,
        length,
        node.session_id,
        sequence,
        transport._randomized_flags(mode=2, proc=3),
    )
    frame[0] = 0x7E
    frame[0x18] = 2
    struct.pack_into("<I", frame, 0x20, message_id & 0x7FFFFFFF)
    struct.pack_into("<H", frame, 0x24, 1)
    frame[0x26] = 7
    frame[0x27] = len(encoded_path)
    struct.pack_into("<H", frame, 0x28, len(encoded_json))
    cursor = 0x2A
    struct.pack_into("<Q", frame, cursor, device_id)
    cursor += 8
    frame[cursor : cursor + len(encoded_path)] = encoded_path
    cursor += len(encoded_path) + 1
    frame[cursor : cursor + len(encoded_json)] = encoded_json
    return transport._finish_mode2(frame, node.session_key)


def _parse_onvif_write_response(frame: bytes, message_id: int) -> int | None:
    if len(frame) < 0x36 or frame[1] != 0xD3:
        return None
    if struct.unpack_from("<I", frame, 0x30)[0] != message_id:
        return None
    return struct.unpack_from("<H", frame, 0x34)[0]


def _exchange_onvif_write(
    sock: socket.socket,
    node: transport.CertifiedNode,
    device: transport.OnlineDevice,
    enabled: bool,
    sequence: int,
    timeout: float,
    *,
    deadline: float,
) -> transport.ModelWriteResult:
    """Send one non-retried ONVIF state write and await its correlated D3."""

    message_id = secrets.randbits(31)
    request = build_onvif_enable_write(node, device.device_id, enabled, sequence, message_id)
    transport_acknowledged = False
    error_code = None
    sock.sendto(request, node.address)
    receive_until = min(time.monotonic() + timeout, deadline)
    for wire, peer in transport._receive(sock, receive_until):
        if peer != node.address:
            continue
        plain = transport._decrypt_node_frame(wire, node)
        if plain is None:
            continue
        flags = struct.unpack_from("<I", plain, 0x14)[0]
        if flags & (1 << 20):
            if plain[1] == 0xD2:
                transport_acknowledged = True
            continue
        candidate = _parse_onvif_write_response(plain, message_id)
        transport.acknowledge_reliable_node_frame(sock, node, plain)
        if candidate is not None:
            error_code = candidate
            break
    return transport.ModelWriteResult(transport_acknowledged, error_code)


def _wait_onvif_state(
    sock: socket.socket,
    node: transport.CertifiedNode,
    device: transport.OnlineDevice,
    expected: bool,
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
        result = transport.exchange_model_read(
            sock,
            node,
            device,
            ONVIF_READ_PATH,
            (sequence + attempt) & 0xFFFFFFFF,
            min(timeout, max(0.5, deadline - time.monotonic())),
            retries=1,
            deadline=deadline,
        )
        if result.error_code == 0 and extract_onvif_enabled(result.value) is expected:
            return True
    return False


def _set_onvif_in_session(
    sock: socket.socket,
    node: transport.CertifiedNode,
    device: transport.OnlineDevice,
    enabled: bool,
    sequence: int,
    timeout: float,
    deadline: float,
) -> tuple[bool, transport.ModelWriteResult | None]:
    preflight = transport.exchange_model_read(
        sock, node, device, ONVIF_READ_PATH, sequence, timeout, deadline=deadline
    )
    previous = extract_onvif_enabled(preflight.value)
    if preflight.error_code != 0 or previous is None:
        raise transport.P2PProbeError("RTSP activation preflight returned no supported state")
    if previous is enabled:
        return previous, None
    write = _exchange_onvif_write(
        sock,
        node,
        device,
        enabled,
        (sequence + 1) & 0xFFFFFFFF,
        timeout,
        deadline=deadline,
    )
    if write.error_code != 0:
        raise transport.P2PProbeError("camera rejected the RTSP activation change")
    if not _wait_onvif_state(
        sock, node, device, enabled, (sequence + 2) & 0xFFFFFFFF, timeout, deadline
    ):
        raise transport.P2PProbeError("camera did not confirm the RTSP activation state")
    return previous, write


def _build_password_request(
    node: transport.CertifiedNode,
    access_id: int,
    device_id: int,
    digest: str,
    sequence: int,
    message_id: int,
    request_id: int,
) -> bytes:
    if len(digest) != 32 or any(char not in string.hexdigits for char in digest):
        raise ValueError("RTSP password digest must contain 32 hexadecimal characters")
    message = {"type": 3, "data": {"password": digest.lower()}}
    encoded = json.dumps(message, separators=(",", ":")).encode()
    payload = b"\x01\xff\x00\x00" + struct.pack("<I", request_id) + encoded
    frame = transport._new_header(
        0xB9,
        0x34 + len(payload),
        node.session_id,
        sequence,
        transport._randomized_flags(mode=2, proc=1),
    )
    frame[0] = 0x7E
    struct.pack_into("<I", frame, 0x18, 2)
    struct.pack_into("<Q", frame, 0x1C, device_id)
    struct.pack_into("<Q", frame, 0x24, access_id)
    struct.pack_into("<I", frame, 0x2C, message_id & 0x7FFFFFFF)
    struct.pack_into("<H", frame, 0x30, len(payload))
    frame[0x34:] = payload
    return transport._finish_mode2(frame, node.session_key)


def _parse_password_response(frame: bytes) -> dict[str, object] | None:
    if len(frame) < 0x3C or frame[1] != 0xB9:
        return None
    payload_length = struct.unpack_from("<H", frame, 0x30)[0]
    if payload_length < 8 or 0x34 + payload_length > len(frame):
        return None
    payload = frame[0x34 : 0x34 + payload_length]
    if payload[:4] != b"\x01\xff\x00\x00":
        return None
    try:
        value = json.loads(payload[8:].decode())
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) and value.get("type") == 3 else None


def _build_password_receipt(
    node: transport.CertifiedNode, response: bytes, sequence: int
) -> bytes:
    response_flags = struct.unpack_from("<I", response, 0x14)[0]
    mode = (response_flags >> 16) & 3
    extra = response_flags & (1 << 25) if mode == 1 else 0
    frame = transport._new_header(
        0xBA,
        0x34,
        node.session_id,
        sequence,
        transport._randomized_flags(mode=mode, proc=1, extra=extra),
    )
    frame[0] = 0x7E
    struct.pack_into("<Q", frame, 0x1C, struct.unpack_from("<Q", response, 0x24)[0])
    struct.pack_into("<Q", frame, 0x24, struct.unpack_from("<Q", response, 0x1C)[0])
    struct.pack_into("<I", frame, 0x2C, struct.unpack_from("<I", response, 0x2C)[0])
    if mode == 2:
        return transport._finish_mode2(frame, node.session_key)
    if mode == 1:
        return transport._finish_mode1(frame)
    raise ValueError("RTSP password receipt requires an encrypted response")


def _exchange_password(
    sock: socket.socket,
    node: transport.CertifiedNode,
    enrollment: P2PEnrollment,
    device: transport.OnlineDevice,
    password: str,
    sequence: int,
    timeout: float,
    deadline: float,
) -> _PasswordExchange:
    """Deliver ``type=3`` exactly once; never retry a credential mutation."""

    message_id = secrets.randbits(31)
    request = _build_password_request(
        node,
        enrollment.access_id,
        device.device_id,
        rtsp_password_digest(password),
        sequence,
        message_id,
        secrets.randbits(32),
    )
    transport_acknowledged = False
    application_acknowledged = False
    response_value = None
    sock.sendto(request, node.address)
    for wire, peer in transport._receive(sock, min(time.monotonic() + timeout, deadline)):
        if peer != node.address:
            continue
        plain = transport._decrypt_node_frame(wire, node)
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
                transport.acknowledge_reliable_node_frame(sock, node, plain)
            continue
        parsed = _parse_password_response(plain)
        if parsed is None:
            continue
        transport.acknowledge_reliable_node_frame(sock, node, plain)
        sock.sendto(
            _build_password_receipt(node, plain, (sequence + 1) & 0xFFFFFFFF),
            node.address,
        )
        response_value = parsed
        break
    return _PasswordExchange(
        transport_acknowledged, application_acknowledged, response_value
    )


def set_camera_rtsp_enabled(
    enrollment: P2PEnrollment,
    enabled: bool,
    *,
    timeout: float = 1.5,
    total_timeout: float = 30.0,
) -> P2PRtspEnableWrite:
    """Set the fixed RTSP enable flag with preflight and fresh readback."""

    if type(enabled) is not bool:
        raise ValueError("RTSP enabled state must be a boolean")
    bounded_timeout = max(0.5, min(float(timeout), 5.0))
    deadline = time.monotonic() + max(10.0, min(float(total_timeout), 40.0))
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("", 0))
    try:
        node, target, sequence = transport._camera_session(
            sock, enrollment, bounded_timeout, deadline
        )
        previous, write = _set_onvif_in_session(
            sock, node, target, enabled, sequence, bounded_timeout, deadline
        )
    except transport.P2PProbeError:
        raise
    except (OSError, ValueError) as exc:
        raise transport.P2PProbeError("P2P RTSP activation failed") from exc
    finally:
        sock.close()
    return P2PRtspEnableWrite(
        device_id=enrollment.device_id,
        enabled=enabled,
        previous_enabled=previous,
        changed=write is not None,
        transport_acknowledged=bool(write and write.transport_acknowledged),
        error_code=write.error_code if write else 0,
        verified=True,
    )


def prepare_camera_rtsp(
    enrollment: P2PEnrollment,
    password: str,
    *,
    timeout: float = 1.5,
    total_timeout: float = 35.0,
) -> P2PRtspPreparation:
    """Enable RTSP and deliver one HA1; LAN media proof remains mandatory afterwards."""

    rtsp_password_digest(password)  # validate before opening any network socket
    bounded_timeout = max(0.5, min(float(timeout), 5.0))
    deadline = time.monotonic() + max(12.0, min(float(total_timeout), 45.0))
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("", 0))
    try:
        node, target, sequence = transport._camera_session(
            sock, enrollment, bounded_timeout, deadline
        )
        previous, enable_write = _set_onvif_in_session(
            sock, node, target, True, sequence, bounded_timeout, deadline
        )
        exchange = _exchange_password(
            sock,
            node,
            enrollment,
            target,
            password,
            (sequence + 0x20) & 0xFFFFFFFF,
            min(5.0, max(0.5, deadline - time.monotonic())),
            deadline,
        )
        delivered = exchange.transport_acknowledged or exchange.application_acknowledged
        if not delivered:
            if not previous:
                try:
                    _set_onvif_in_session(
                        sock,
                        node,
                        target,
                        False,
                        (sequence + 0x40) & 0xFFFFFFFF,
                        bounded_timeout,
                        deadline,
                    )
                except transport.P2PProbeError:
                    pass
            raise transport.P2PProbeError("RTSP credential delivery was not acknowledged")
        accepted = bool(
            exchange.response is not None
            and exchange.response.get("type") == 3
            and exchange.response.get("err", 0) == 0
        )
    except transport.P2PProbeError:
        raise
    except (OSError, ValueError) as exc:
        raise transport.P2PProbeError("P2P RTSP credential setup failed") from exc
    finally:
        sock.close()
    return P2PRtspPreparation(
        device_id=enrollment.device_id,
        previous_enabled=previous,
        enabled_changed=enable_write is not None,
        password_delivery_acknowledged=True,
        password_response_accepted=accepted,
    )
