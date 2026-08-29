"""Bounded GDM property-read codec for the Yoosee IoTVideo control plane."""

from __future__ import annotations

import json
import struct

from .contracts import MODEL_READ_PATHS, CertifiedNode
from .wire import finish_mode2, new_header, randomized_flags


def build_model_read(
    node: CertifiedNode,
    device_id: int,
    path: str,
    sequence: int,
    message_id: int,
) -> bytes:
    """Build one allowlisted, read-only GDM B7 property request."""
    if path not in MODEL_READ_PATHS:
        raise ValueError("thing-model path is not in the read-only allowlist")
    encoded_path = path.encode("utf-8")
    frame = new_header(
        0xB7,
        0x26 + len(encoded_path) + 1,
        node.session_id,
        sequence,
        randomized_flags(mode=2, proc=3),
    )
    frame[0] = 0x7E
    struct.pack_into("<Q", frame, 0x18, device_id)
    struct.pack_into("<I", frame, 0x20, message_id & 0x7FFFFFFF)
    struct.pack_into("<H", frame, 0x24, len(encoded_path))
    frame[0x26 : 0x26 + len(encoded_path)] = encoded_path
    return finish_mode2(frame, node.session_key)


def parse_model_read_response(frame: bytes, device_id: int) -> tuple[int, object | None] | None:
    """Parse direct B8 or access-node cached AA GDM responses."""
    if len(frame) < 0x26 or frame[1] not in (0xAA, 0xB8):
        return None
    if struct.unpack_from("<Q", frame, 0x18)[0] != device_id:
        return None
    error_code = struct.unpack_from("<H", frame, 0x24)[0]
    if not (frame[0x20] & 1):
        return error_code, None
    if len(frame) < 0x28:
        return None
    json_length = struct.unpack_from("<H", frame, 0x26)[0]
    if 0x28 + json_length > len(frame):
        return None
    try:
        value = json.loads(frame[0x28 : 0x28 + json_length].decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    return error_code, value


def parse_model_report(frame: bytes) -> tuple[int | None, str, object] | None:
    """Parse a brokered AA property report without accepting an action response."""
    if len(frame) < 0x22 or frame[1] != 0xAA:
        return None
    options = struct.unpack_from("<H", frame, 0x1C)[0]
    path_length = frame[0x1F] + 1
    json_length = struct.unpack_from("<H", frame, 0x20)[0] + 1
    cursor = 0x22
    destination = None
    if options & 1:
        if cursor + 8 > len(frame):
            return None
        destination = struct.unpack_from("<Q", frame, cursor)[0]
        cursor += 8
    if cursor + path_length + json_length > len(frame):
        return None
    encoded_path = frame[cursor : cursor + path_length].rstrip(b"\x00")
    cursor += path_length
    encoded_json = frame[cursor : cursor + json_length].rstrip(b"\x00")
    try:
        path = encoded_path.decode("utf-8")
        value = json.loads(encoded_json.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    return (destination, path, value) if path else None
