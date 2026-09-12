"""Fixed modern PTZ codec. No sockets, fallback, motion session or capability grants."""
from __future__ import annotations

import json
import struct
from dataclasses import dataclass

from .contracts import CertifiedNode
from .wire import finish_mode2, new_header, randomized_flags

DIRECTIONS = {"left": 0, "right": 1, "up": 2, "down": 3}


def direction_supported(observation: object, direction: str) -> bool:
    """Read only the exact devInfo root/first head; never borrow another head's bits."""
    if direction not in DIRECTIONS or not isinstance(observation, dict):
        return False
    timestamp = observation.get("t")
    value = observation.get("stVal")
    if type(timestamp) is not int or not 0 < timestamp <= 0x7FFFFFFF or not isinstance(value, dict):
        return False
    info = value.get("ptzInfo")
    status = info.get("id0_status") if isinstance(info, dict) else None
    if type(status) is not int or not 0 <= status <= 0x7FFFFFFF:
        return False
    axis = 2 if direction in ("left", "right") else 4
    return bool(status & 1 and status & axis)


def build_ptz_request(node: CertifiedNode, access_id: int, device_id: int, direction: str,
                      pressed: bool, sequence: int, message_id: int, request_id: int) -> bytes:
    """Encode only type-2 START/RELEASE, never presets, patrol or calibration."""
    if direction not in DIRECTIONS or type(pressed) is not bool:
        raise ValueError("PTZ requires a known direction and boolean pressed state")
    data = {"type": 2, "data": {"dir": DIRECTIONS[direction], "touchType": 0 if pressed else 1}}
    payload = b"\x01\xff\x00\x00" + struct.pack("<I", request_id) + json.dumps(
        data, separators=(",", ":"),
    ).encode()
    frame = new_header(0xB9, 0x34 + len(payload), node.session_id, sequence,
                       randomized_flags(mode=2, proc=1))
    frame[0] = 0x7E
    struct.pack_into("<IQQIH", frame, 0x18, 2, device_id, access_id, message_id & 0x7FFFFFFF, len(payload))
    frame[0x34:] = payload
    return finish_mode2(frame, node.session_key)


@dataclass(frozen=True, slots=True)
class PtzReply:
    transport_receipt: bool = False
    peer_receipt: bool = False
    error_code: int | None = None


def parse_ptz_reply(frame: bytes, *, node: CertifiedNode, access_id: int, device_id: int,
                    sequence: int, message_id: int, request_id: int) -> PtzReply | None:
    """Parse decrypted mode-2 replies only after caller verifies UDP peer/decryption.

    Receipts acknowledge delivery, never physical movement/stop. A session owner must
    combine receipts for the same request and give an explicit error precedence.
    """
    if not 0x18 <= len(frame) <= 4096 or frame[0] != 0x7E:
        return None
    flags = struct.unpack_from("<I", frame, 0x14)[0]
    if ((flags >> 16) & 3) != 2 or struct.unpack_from("<Q", frame, 4)[0] != node.session_id:
        return None
    if flags & (1 << 20):
        if frame[1] != 0xB9 or struct.unpack_from("<I", frame, 0x0C)[0] != (sequence & 0xFFFFFFFF):
            return None
        return PtzReply(transport_receipt=True)
    if len(frame) < 0x34 or frame[1] not in (0xB9, 0xBA):
        return None
    if (struct.unpack_from("<Q", frame, 0x1C)[0] != access_id
            or struct.unpack_from("<Q", frame, 0x24)[0] != device_id):
        return None
    if frame[1] == 0xBA:
        if struct.unpack_from("<I", frame, 0x2C)[0] != (message_id & 0x7FFFFFFF):
            return None
        return PtzReply(peer_receipt=True)
    length = struct.unpack_from("<H", frame, 0x30)[0]
    if length < 8 or 0x34 + length > len(frame):
        return None
    payload = frame[0x34:0x34 + length]
    if payload[:4] not in (b"\x01\xff\x00\x00", b"\x01\x00\x00\x00"):
        return None
    if struct.unpack_from("<I", payload, 4)[0] != request_id:
        return None
    try:
        value = json.loads(payload[8:])
    except (ValueError, UnicodeError, RecursionError):
        return None
    if (not isinstance(value, dict) or type(value.get("type")) is not int or value["type"] != 2
            or type(value.get("err")) is not int):
        return None
    return PtzReply(error_code=value["err"])
