"""Typed white-floodlight control over the proven IoTVideo passthrough contract.

This module deliberately exposes no generic JSON sender.  It can only read type 12 or write the
binary type 11 ON/OFF state, always for the exact device in a durable P2P enrollment.
"""

from __future__ import annotations

import json
import secrets
import socket
import struct
import time
from dataclasses import dataclass

from ....db.p2p import P2PEnrollment
from . import client as transport
from .session_io import acknowledge_reliable_node_frame, decrypt_node_frame, receive_datagrams
from .wire import finish_mode1, finish_mode2, new_header, randomized_flags


@dataclass(frozen=True, slots=True)
class WhiteLightExchange:
    transport_acknowledged: bool
    application_acknowledged: bool
    response: dict[str, object] | None


@dataclass(frozen=True, slots=True)
class P2PWhiteLightState:
    device_id: str
    enabled: bool
    authenticated: bool
    direct_handshake: bool
    transport_acknowledged: bool
    application_acknowledged: bool


@dataclass(frozen=True, slots=True)
class P2PWhiteLightWrite:
    device_id: str
    enabled: bool
    previous_enabled: bool
    changed: bool
    transport_acknowledged: bool
    application_acknowledged: bool
    verified: bool


def build_white_light_request(
    node: transport.CertifiedNode,
    access_id: int,
    device_id: int,
    enabled: bool | None,
    sequence: int,
    message_id: int,
    request_id: int,
) -> bytes:
    """Build only the proven type-12 read or type-11 floodlight ON/OFF request."""

    if enabled is not None and type(enabled) is not bool:
        raise ValueError("white-light state must be a boolean or None for a status read")
    message: dict[str, object]
    if enabled is None:
        message = {"type": 12}
    else:
        state = int(enabled)
        message = {
            "data": {"whiteLightCtrl": state, "whiteLightStatus": 0},
            "type": 11,
        }
    encoded = json.dumps(message, separators=(",", ":")).encode("utf-8")
    payload = b"\x01\xff\x00\x00" + struct.pack("<I", request_id) + encoded
    frame = new_header(
        0xB9,
        0x34 + len(payload),
        node.session_id,
        sequence,
        randomized_flags(mode=2, proc=1),
    )
    frame[0] = 0x7E
    struct.pack_into("<I", frame, 0x18, 2)
    struct.pack_into("<Q", frame, 0x1C, device_id)
    struct.pack_into("<Q", frame, 0x24, access_id)
    struct.pack_into("<I", frame, 0x2C, message_id & 0x7FFFFFFF)
    struct.pack_into("<H", frame, 0x30, len(payload))
    frame[0x34:] = payload
    return finish_mode2(frame, node.session_key)


def parse_white_light_response(
    frame: bytes, expected_type: int
) -> tuple[int, dict[str, object]] | None:
    """Parse only a full type-11/type-12 response from the passthrough family."""

    if expected_type not in (11, 12) or len(frame) < 0x3C or frame[1] != 0xB9:
        return None
    payload_length = struct.unpack_from("<H", frame, 0x30)[0]
    if payload_length < 8 or 0x34 + payload_length > len(frame):
        return None
    payload = frame[0x34 : 0x34 + payload_length]
    if payload[:4] != b"\x01\xff\x00\x00":
        return None
    try:
        value = json.loads(payload[8:].decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    if not isinstance(value, dict) or value.get("type") != expected_type:
        return None
    return struct.unpack_from("<I", payload, 4)[0], value


def extract_white_light_state(response: dict[str, object] | None) -> bool | None:
    """Extract the selected model's binary lamp state from a type-12 response."""

    if response is None or response.get("type") != 12:
        return None
    data = response.get("data")
    if not isinstance(data, dict):
        return None
    state = data.get("whiteLightStatus")
    if type(state) is not int or state not in (0, 1):
        return None
    return bool(state)


def build_white_light_receipt(
    node: transport.CertifiedNode, response: bytes, sequence: int
) -> bytes:
    """Build the full BA application receipt required by a white-light B9 response."""

    if len(response) < 0x34 or response[1] != 0xB9:
        raise ValueError("white-light receipt requires a full B9 response")
    response_flags = struct.unpack_from("<I", response, 0x14)[0]
    mode = (response_flags >> 16) & 3
    extra = response_flags & (1 << 25) if mode == 1 else 0
    frame = new_header(
        0xBA,
        0x34,
        node.session_id,
        sequence,
        randomized_flags(mode=mode, proc=1, extra=extra),
    )
    frame[0] = 0x7E
    struct.pack_into("<Q", frame, 0x1C, struct.unpack_from("<Q", response, 0x24)[0])
    struct.pack_into("<Q", frame, 0x24, struct.unpack_from("<Q", response, 0x1C)[0])
    struct.pack_into("<I", frame, 0x2C, struct.unpack_from("<I", response, 0x2C)[0])
    if mode == 2:
        return finish_mode2(frame, node.session_key)
    if mode == 1:
        return finish_mode1(frame)
    raise ValueError("white-light receipt requires an encrypted response")


def exchange_white_light(
    sock: socket.socket,
    node: transport.CertifiedNode,
    access_id: int,
    device: transport.OnlineDevice,
    enabled: bool | None,
    sequence: int,
    timeout: float,
    *,
    retries: int = 3,
    deadline: float | None = None,
) -> WhiteLightExchange:
    """Exchange only a typed floodlight state read or ON/OFF command."""

    if enabled is not None and type(enabled) is not bool:
        raise ValueError("white-light state must be a boolean or None for a status read")
    if retries < 1:
        raise ValueError("white-light retries must be positive")
    message_id = secrets.randbits(31)
    request = build_white_light_request(
        node,
        access_id,
        device.device_id,
        enabled,
        sequence,
        message_id,
        secrets.randbits(32),
    )
    expected_type = 12 if enabled is None else 11
    transport_acknowledged = False
    application_acknowledged = False
    response_value = None
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
            parsed = parse_white_light_response(plain, expected_type)
            if parsed is None:
                continue
            acknowledge_reliable_node_frame(sock, node, plain)
            sock.sendto(
                build_white_light_receipt(
                    node,
                    plain,
                    (sequence + retry + 1) & 0xFFFFFFFF,
                ),
                node.address,
            )
            _incoming_request_id, response_value = parsed
            break
        if response_value is not None:
            break
    return WhiteLightExchange(
        transport_acknowledged,
        application_acknowledged,
        response_value,
    )


def read_camera_white_light(
    enrollment: P2PEnrollment,
    *,
    timeout: float = 1.5,
    total_timeout: float = 25.0,
) -> P2PWhiteLightState:
    """Read the selected camera's floodlight state without exposing passthrough JSON."""

    bounded_timeout = max(0.5, min(float(timeout), 5.0))
    deadline = time.monotonic() + max(8.0, min(float(total_timeout), 35.0))
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("", 0))
    try:
        node, target, sequence = transport._camera_session(
            sock, enrollment, bounded_timeout, deadline
        )
        result = exchange_white_light(
            sock,
            node,
            enrollment.access_id,
            target,
            None,
            sequence,
            min(5.0, max(0.5, deadline - time.monotonic())),
            deadline=deadline,
        )
        enabled = extract_white_light_state(result.response)
        if enabled is None:
            raise transport.P2PProbeError("camera returned no supported white-light state")
    except transport.P2PProbeError:
        raise
    except (OSError, ValueError) as exc:
        raise transport.P2PProbeError("P2P white-light state read failed") from exc
    finally:
        sock.close()
    return P2PWhiteLightState(
        device_id=enrollment.device_id,
        enabled=enabled,
        authenticated=True,
        direct_handshake=True,
        transport_acknowledged=result.transport_acknowledged,
        application_acknowledged=result.application_acknowledged,
    )


def set_camera_white_light(
    enrollment: P2PEnrollment,
    enabled: bool,
    *,
    timeout: float = 1.5,
    total_timeout: float = 30.0,
) -> P2PWhiteLightWrite:
    """Set the floodlight after exact-target preflight, then require fresh readback."""

    if type(enabled) is not bool:
        raise ValueError("white-light state must be a boolean")
    bounded_timeout = max(0.5, min(float(timeout), 5.0))
    deadline = time.monotonic() + max(10.0, min(float(total_timeout), 40.0))
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("", 0))
    try:
        node, target, sequence = transport._camera_session(
            sock, enrollment, bounded_timeout, deadline
        )
        preflight = exchange_white_light(
            sock,
            node,
            enrollment.access_id,
            target,
            None,
            sequence,
            min(5.0, max(0.5, deadline - time.monotonic())),
            deadline=deadline,
        )
        previous = extract_white_light_state(preflight.response)
        if previous is None:
            raise transport.P2PProbeError(
                "camera white-light preflight returned no supported state"
            )
        if previous is enabled:
            return P2PWhiteLightWrite(
                enrollment.device_id,
                enabled,
                previous,
                False,
                False,
                False,
                True,
            )

        # Actuation is intentionally never retried: a lost response must not duplicate a command.
        write = exchange_white_light(
            sock,
            node,
            enrollment.access_id,
            target,
            enabled,
            (sequence + 1) & 0xFFFFFFFF,
            min(5.0, max(0.5, deadline - time.monotonic())),
            retries=1,
            deadline=deadline,
        )
        error = write.response.get("err") if write.response is not None else None
        if type(error) is not int or error != 0:
            raise transport.P2PProbeError("camera rejected the white-light change")

        verified = False
        for attempt in range(5):
            if attempt:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                time.sleep(min(0.4, remaining))
            readback = exchange_white_light(
                sock,
                node,
                enrollment.access_id,
                target,
                None,
                (sequence + 2 + attempt) & 0xFFFFFFFF,
                min(2.0, max(0.5, deadline - time.monotonic())),
                retries=1,
                deadline=deadline,
            )
            if extract_white_light_state(readback.response) is enabled:
                verified = True
                break
        if not verified:
            raise transport.P2PProbeError("camera did not confirm the white-light change")
    except transport.P2PProbeError:
        raise
    except (OSError, ValueError) as exc:
        raise transport.P2PProbeError("P2P white-light change failed") from exc
    finally:
        sock.close()
    return P2PWhiteLightWrite(
        device_id=enrollment.device_id,
        enabled=enabled,
        previous_enabled=previous,
        changed=True,
        transport_acknowledged=write.transport_acknowledged,
        application_acknowledged=write.application_acknowledged,
        verified=True,
    )
