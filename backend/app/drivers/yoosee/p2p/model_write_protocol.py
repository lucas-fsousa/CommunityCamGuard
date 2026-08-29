"""Internal scalar D2/D3 thing-model codec.

Feature modules remain responsible for fixed paths and semantic allowlists.  This module only
encodes their already-validated scalar value and correlates the application response.
"""

from __future__ import annotations

import json
import struct

from .contracts import CertifiedNode
from .wire import finish_mode2, new_header, randomized_flags

ScalarModelValue = int | str


def build_model_write(
    node: CertifiedNode,
    device_id: int,
    path: str,
    value: ScalarModelValue,
    sequence: int,
    message_id: int,
) -> bytes:
    """Encode one target-type-7 scalar write selected by a typed feature module."""

    if not path.startswith("ProWritable.") or ".setVal" not in path:
        raise ValueError("model write path must be a writable setVal leaf")
    if type(value) not in {int, str}:
        raise ValueError("model write value must be an integer or string scalar")
    encoded_path = path.encode("utf-8")
    encoded_json = json.dumps(value, ensure_ascii=True, separators=(",", ":")).encode("ascii")
    if len(encoded_path) > 0xFF or len(encoded_json) > 0xFFFF:
        raise ValueError("model write path or value exceeds the wire format")

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


def parse_model_write_response(frame: bytes, message_id: int) -> int | None:
    """Return the D3 error code only for the matching application request."""

    if len(frame) < 0x36 or frame[1] != 0xD3:
        return None
    if struct.unpack_from("<I", frame, 0x30)[0] != message_id:
        return None
    return struct.unpack_from("<H", frame, 0x34)[0]
