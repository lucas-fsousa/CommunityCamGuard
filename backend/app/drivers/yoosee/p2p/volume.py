"""Typed Yoosee camera-speaker volume control."""

from __future__ import annotations

import secrets
import socket
import struct
import time
from dataclasses import dataclass

from ....db.p2p import P2PEnrollment
from .camera_session import open_camera_session
from .contracts import CertifiedNode, ModelWriteResult, OnlineDevice, P2PProbeError
from .model_session import exchange_model_read
from .session_io import (
    acknowledge_reliable_node_frame,
    decrypt_node_frame,
    receive_datagrams,
)
from .wire import finish_mode2, new_header, randomized_flags

VOLUME_READ_PATH = "ProWritable.volume"
VOLUME_WRITE_PATH = "ProWritable.volume.setVal"
VOLUME_RAW_BY_PERCENT = {0: 0, 25: 2, 50: 5, 75: 7, 100: 10}


@dataclass(frozen=True, slots=True)
class P2PSpeakerVolumeWrite:
    device_id: str
    volume_percent: int
    previous_percent: int
    previous_raw: int
    requested_raw: int
    changed: bool
    transport_acknowledged: bool
    error_code: int | None
    verified: bool


@dataclass(frozen=True, slots=True)
class P2PSpeakerVolumeState:
    device_id: str
    volume_percent: int
    raw_value: int
    authenticated: bool
    direct_handshake: bool
    transport_acknowledged: bool
    error_code: int | None


def build_volume_write(
    node: CertifiedNode,
    device_id: int,
    volume_percent: int,
    sequence: int,
    message_id: int,
) -> bytes:
    """Build only the recovered five-position speaker-volume D2 write."""

    if type(volume_percent) is not int or volume_percent not in VOLUME_RAW_BY_PERCENT:
        raise ValueError("speaker volume must be 0, 25, 50, 75 or 100 percent")
    encoded_path = VOLUME_WRITE_PATH.encode("utf-8")
    encoded_json = str(VOLUME_RAW_BY_PERCENT[volume_percent]).encode("ascii")
    length = 0x2A + 8 + len(encoded_path) + 1 + len(encoded_json) + 1
    frame = new_header(
        0xD2,
        length,
        node.session_id,
        sequence,
        randomized_flags(mode=2, proc=3),
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
    return finish_mode2(frame, node.session_key)


def parse_volume_write_response(frame: bytes, message_id: int) -> int | None:
    if len(frame) < 0x36 or frame[1] != 0xD3:
        return None
    if struct.unpack_from("<I", frame, 0x30)[0] != message_id:
        return None
    return struct.unpack_from("<H", frame, 0x34)[0]


def extract_volume_raw(value: object) -> int | None:
    """Extract a firmware speaker-volume value in its documented 0..10 range."""

    if isinstance(value, int) and not isinstance(value, bool):
        return value if 0 <= value <= 10 else None
    if isinstance(value, dict):
        for key in ("setVal", "value", "v"):
            direct = value.get(key)
            if isinstance(direct, int) and not isinstance(direct, bool):
                if 0 <= direct <= 10:
                    return direct
        for key, nested in value.items():
            if key in {"t", "time", "timestamp"}:
                continue
            candidate = extract_volume_raw(nested)
            if candidate is not None:
                return candidate
    return None


def volume_percent(raw: int) -> int:
    """Apply the APK's display buckets to one raw 0..10 volume value."""

    if type(raw) is not int or not 0 <= raw <= 10:
        raise ValueError("raw speaker volume must be between 0 and 10")
    if raw == 0:
        return 0
    if raw <= 2:
        return 25
    if raw <= 5:
        return 50
    if raw <= 7:
        return 75
    return 100


def exchange_volume_write(
    sock: socket.socket,
    node: CertifiedNode,
    device: OnlineDevice,
    volume: int,
    sequence: int,
    timeout: float,
    *,
    retries: int = 3,
    deadline: float | None = None,
) -> ModelWriteResult:
    if retries < 1:
        raise ValueError("volume-write retries must be positive")
    message_id = secrets.randbits(31)
    request = build_volume_write(node, device.device_id, volume, sequence, message_id)
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
                if plain[1] == 0xD2:
                    transport_acknowledged = True
                continue
            candidate = parse_volume_write_response(plain, message_id)
            acknowledge_reliable_node_frame(sock, node, plain)
            if candidate is not None:
                error_code = candidate
                break
        if error_code is not None:
            break
    return ModelWriteResult(transport_acknowledged, error_code)


def read_camera_speaker_volume(
    enrollment: P2PEnrollment,
    *,
    timeout: float = 1.5,
    total_timeout: float = 25.0,
) -> P2PSpeakerVolumeState:
    """Read the selected camera's speaker-volume position on explicit request."""

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
            VOLUME_READ_PATH,
            sequence,
            min(5.0, max(0.5, deadline - time.monotonic())),
            deadline=deadline,
        )
        raw = extract_volume_raw(result.value)
        if result.error_code != 0 or raw is None:
            raise P2PProbeError("camera returned no supported speaker-volume state")
    except P2PProbeError:
        raise
    except (OSError, ValueError) as exc:
        raise P2PProbeError("P2P speaker-volume read failed") from exc
    finally:
        sock.close()
    return P2PSpeakerVolumeState(
        enrollment.device_id,
        volume_percent(raw),
        raw,
        True,
        True,
        result.transport_acknowledged,
        result.error_code,
    )


def set_camera_speaker_volume(
    enrollment: P2PEnrollment,
    volume: int,
    *,
    timeout: float = 1.5,
    total_timeout: float = 30.0,
) -> P2PSpeakerVolumeWrite:
    """Set one APK-defined speaker-volume position with preflight and fresh readback."""

    if type(volume) is not int or volume not in VOLUME_RAW_BY_PERCENT:
        raise ValueError("speaker volume must be 0, 25, 50, 75 or 100 percent")
    requested_raw = VOLUME_RAW_BY_PERCENT[volume]
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
            VOLUME_READ_PATH,
            sequence,
            min(5.0, max(0.5, deadline - time.monotonic())),
            deadline=deadline,
        )
        previous_raw = extract_volume_raw(preflight.value)
        if preflight.error_code != 0 or previous_raw is None:
            raise P2PProbeError("speaker-volume preflight returned no supported state")
        previous_percent = volume_percent(previous_raw)
        if previous_percent == volume:
            return P2PSpeakerVolumeWrite(
                enrollment.device_id,
                volume,
                previous_percent,
                previous_raw,
                requested_raw,
                False,
                False,
                0,
                True,
            )

        write = exchange_volume_write(
            sock,
            node,
            target,
            volume,
            (sequence + 1) & 0xFFFFFFFF,
            bounded_timeout,
            deadline=deadline,
        )
        if write.error_code != 0:
            raise P2PProbeError("camera rejected the speaker-volume change")
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
                VOLUME_READ_PATH,
                (sequence + 2 + attempt) & 0xFFFFFFFF,
                min(bounded_timeout, max(0.5, deadline - time.monotonic())),
                retries=1,
                deadline=deadline,
            )
            if readback.error_code == 0 and extract_volume_raw(readback.value) == requested_raw:
                verified = True
                break
        if not verified:
            raise P2PProbeError("camera did not confirm the speaker-volume change")
    except P2PProbeError:
        raise
    except (OSError, ValueError) as exc:
        raise P2PProbeError("P2P speaker-volume change failed") from exc
    finally:
        sock.close()
    return P2PSpeakerVolumeWrite(
        enrollment.device_id,
        volume,
        previous_percent,
        previous_raw,
        requested_raw,
        True,
        write.transport_acknowledged,
        write.error_code,
        True,
    )
