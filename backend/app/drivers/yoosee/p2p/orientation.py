"""Typed normal/180-degree image orientation control.

The module deliberately owns the complete orientation write surface.  It accepts only the two
proven states, performs a read preflight, sends the fixed D2 property and requires fresh readback.
No generic thing-model writer is exposed.
"""

from __future__ import annotations

import secrets
import socket
import struct
import time
from dataclasses import dataclass

from ....db.p2p import P2PEnrollment
from . import client as transport
from .camera_session import open_camera_session
from .session_io import acknowledge_reliable_node_frame, decrypt_node_frame, receive_datagrams
from .wire import finish_mode2, new_header, randomized_flags

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
    node: transport.CertifiedNode,
    device_id: int,
    orientation: str,
    sequence: int,
    message_id: int,
) -> bytes:
    """Build only the proven normal/180-degree D2 property write."""

    if orientation not in ORIENTATION_VALUES:
        raise ValueError("orientation must be normal or inverted")
    encoded_path = ORIENTATION_PATH.encode("utf-8")
    encoded_json = str(ORIENTATION_VALUES[orientation]).encode("ascii")
    length = 0x2A + 8 + len(encoded_path) + 1 + len(encoded_json) + 1
    frame = new_header(
        0xD2,
        length,
        node.session_id,
        sequence,
        randomized_flags(mode=2, proc=3),
    )
    frame[0] = 0x7E
    frame[0x18] = 2  # native target type 7 minus 5
    struct.pack_into("<I", frame, 0x20, message_id & 0x7FFFFFFF)
    struct.pack_into("<H", frame, 0x24, 1)  # destination id is present
    frame[0x26] = 7  # ordinary ProWritable update
    frame[0x27] = len(encoded_path)
    struct.pack_into("<H", frame, 0x28, len(encoded_json))
    cursor = 0x2A
    struct.pack_into("<Q", frame, cursor, device_id)
    cursor += 8
    frame[cursor : cursor + len(encoded_path)] = encoded_path
    cursor += len(encoded_path) + 1
    frame[cursor : cursor + len(encoded_json)] = encoded_json
    return finish_mode2(frame, node.session_key)


def parse_orientation_write_response(frame: bytes, message_id: int) -> int | None:
    """Return the D3 error code only when it matches this orientation request."""

    if len(frame) < 0x36 or frame[1] != 0xD3:
        return None
    if struct.unpack_from("<I", frame, 0x30)[0] != message_id:
        return None
    return struct.unpack_from("<H", frame, 0x34)[0]


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
    node: transport.CertifiedNode,
    device: transport.OnlineDevice,
    orientation: str,
    sequence: int,
    timeout: float,
    *,
    retries: int = 3,
    deadline: float | None = None,
) -> transport.ModelWriteResult:
    """Send one typed D2 orientation write and wait for its matching D3 response."""

    if orientation not in ORIENTATION_VALUES:
        raise ValueError("orientation must be normal or inverted")
    if retries < 1:
        raise ValueError("orientation-write retries must be positive")
    message_id = secrets.randbits(31)
    request = build_orientation_write(node, device.device_id, orientation, sequence, message_id)
    transport_acknowledged = False
    error_code = None
    for _retry in range(retries):
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
                if plain[1] == 0xD2:
                    transport_acknowledged = True
                continue
            candidate = parse_orientation_write_response(plain, message_id)
            acknowledge_reliable_node_frame(sock, node, plain)
            if candidate is not None:
                error_code = candidate
                break
        if error_code is not None:
            break
    return transport.ModelWriteResult(transport_acknowledged, error_code)


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
        preflight = transport.exchange_model_read(
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
            raise transport.P2PProbeError(
                "camera orientation preflight returned no supported state"
            )
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
            raise transport.P2PProbeError("camera rejected the orientation change")

        verified = False
        for attempt in range(5):
            if attempt:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                time.sleep(min(0.4, remaining))
            readback = transport.exchange_model_read(
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
            raise transport.P2PProbeError("camera did not confirm the orientation change")
    except transport.P2PProbeError:
        raise
    except (OSError, ValueError) as exc:
        raise transport.P2PProbeError("P2P orientation change failed") from exc
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
