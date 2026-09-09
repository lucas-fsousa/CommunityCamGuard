"""Strict B9 carrier codec for read-only Yoosee onboard-playback messages."""

from __future__ import annotations

import struct
from typing import Final

from .contracts import CertifiedNode
from .onboard_playback_dates import PLAYBACK_GET_DATE_LIST_COMMAND
from .onboard_playback_modern import (
    PLAYBACK_GET_LIST_V1_COMMAND,
    PLAYBACK_GET_LIST_V2_COMMAND,
)
from .onboard_playback_types import PLAYBACK_GET_RECORDING_TYPES_COMMAND
from .stream_protocol import parse_builtin_command
from .wire import finish_mode1, finish_mode2, new_header, randomized_flags

_READ_ONLY_COMMANDS: Final = frozenset(
    {
        PLAYBACK_GET_LIST_V1_COMMAND,
        PLAYBACK_GET_RECORDING_TYPES_COMMAND,
        PLAYBACK_GET_LIST_V2_COMMAND,
        PLAYBACK_GET_DATE_LIST_COMMAND,
    }
)
_MAX_MESSAGE_SIZE: Final = 0x7800


def build_onboard_playback_carrier(
    node: CertifiedNode,
    access_id: int,
    device_id: int,
    sequence: int,
    message_id: int,
    message: bytes,
) -> bytes:
    """Wrap one allowlisted read message in the native brokered B9 envelope."""

    parsed = parse_builtin_command(message)
    if parsed.command not in _READ_ONLY_COMMANDS:
        raise ValueError("onboard playback carrier command is not read-only allowlisted")
    if not 8 <= len(message) <= _MAX_MESSAGE_SIZE:
        raise ValueError("onboard playback carrier message size is invalid")
    if type(message_id) is not int or not 0 < message_id <= 0x7FFFFFFF:
        raise ValueError("onboard playback carrier message ID is invalid")
    if type(access_id) is not int or not 0 < access_id <= 0xFFFFFFFFFFFFFFFF:
        raise ValueError("onboard playback carrier access ID is invalid")
    if type(device_id) is not int or not 0 < device_id <= 0xFFFFFFFFFFFFFFFF:
        raise ValueError("onboard playback carrier device ID is invalid")

    frame = new_header(
        0xB9,
        0x34 + len(message),
        node.session_id,
        sequence,
        randomized_flags(mode=2, proc=1),
    )
    frame[0] = 0x7E
    struct.pack_into("<I", frame, 0x18, 2)
    struct.pack_into("<Q", frame, 0x1C, device_id)
    struct.pack_into("<Q", frame, 0x24, access_id)
    struct.pack_into("<I", frame, 0x2C, message_id)
    struct.pack_into("<H", frame, 0x30, len(message))
    frame[0x34:] = message
    return finish_mode2(frame, node.session_key)


def unwrap_onboard_playback_carrier(
    frame: bytes,
    *,
    expected_access_id: int,
    expected_device_id: int,
) -> bytes:
    """Validate a decrypted camera-to-client B9 and return its BuiltIn message."""

    if len(frame) < 0x3C or frame[0] not in (0x7E, 0x7F) or frame[1] != 0xB9:
        raise ValueError("onboard playback response is not a B9 message")
    if struct.unpack_from("<H", frame, 2)[0] != len(frame):
        raise ValueError("onboard playback response length is inconsistent")
    if struct.unpack_from("<I", frame, 0x14)[0] & (1 << 20):
        raise ValueError("onboard playback transport receipt is not an application response")
    if struct.unpack_from("<Q", frame, 0x1C)[0] != expected_access_id:
        raise ValueError("onboard playback response destination does not match")
    if struct.unpack_from("<Q", frame, 0x24)[0] != expected_device_id:
        raise ValueError("onboard playback response source does not match")
    payload_size = struct.unpack_from("<H", frame, 0x30)[0]
    if payload_size < 8 or 0x34 + payload_size != len(frame):
        raise ValueError("onboard playback response payload size is inconsistent")
    message = frame[0x34:]
    parse_builtin_command(message)
    return message


def is_onboard_playback_transport_ack(frame: bytes, *, expected_sequence: int) -> bool:
    """Match the reliable 32-byte ACK for one outbound playback B9."""

    if type(expected_sequence) is not int or not 0 <= expected_sequence <= 0xFFFFFFFF:
        raise ValueError("onboard playback sequence is invalid")
    if len(frame) != 0x20 or frame[0] not in (0x7E, 0x7F) or frame[1] != 0xB9:
        return False
    if struct.unpack_from("<H", frame, 2)[0] != len(frame):
        return False
    flags = struct.unpack_from("<I", frame, 0x14)[0]
    return bool(flags & (1 << 20)) and struct.unpack_from("<I", frame, 0x0C)[0] == expected_sequence


def is_onboard_playback_peer_receipt(
    frame: bytes,
    *,
    expected_access_id: int,
    expected_device_id: int,
    expected_message_id: int,
) -> bool:
    """Match the camera's full BA receipt for one outbound playback B9."""

    if len(frame) != 0x34 or frame[0] not in (0x7E, 0x7F) or frame[1] != 0xBA:
        return False
    if struct.unpack_from("<H", frame, 2)[0] != len(frame):
        return False
    if struct.unpack_from("<I", frame, 0x14)[0] & (1 << 20):
        return False
    return (
        struct.unpack_from("<Q", frame, 0x1C)[0] == expected_access_id
        and struct.unpack_from("<Q", frame, 0x24)[0] == expected_device_id
        and struct.unpack_from("<I", frame, 0x2C)[0] == expected_message_id
    )


def build_onboard_playback_receipt(
    node: CertifiedNode,
    response: bytes,
    sequence: int,
    *,
    expected_access_id: int,
    expected_device_id: int,
) -> bytes:
    """Build the native BA receipt for one validated camera playback response."""

    unwrap_onboard_playback_carrier(
        response,
        expected_access_id=expected_access_id,
        expected_device_id=expected_device_id,
    )
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
    struct.pack_into("<Q", frame, 0x1C, expected_device_id)
    struct.pack_into("<Q", frame, 0x24, expected_access_id)
    struct.pack_into("<I", frame, 0x2C, struct.unpack_from("<I", response, 0x2C)[0])
    if mode == 2:
        return finish_mode2(frame, node.session_key)
    if mode == 1:
        return finish_mode1(frame)
    raise ValueError("onboard playback receipt requires an encrypted response")
