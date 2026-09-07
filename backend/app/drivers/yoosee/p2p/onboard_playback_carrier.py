"""Pure B9 carrier for recovered Yoosee IoTVideo onboard-playback messages."""

from __future__ import annotations

import struct

from ...contracts import OnboardRecordingQuery
from .contracts import CertifiedNode
from .onboard_playback_dates import (
    PLAYBACK_GET_DATE_LIST_COMMAND,
    ModernPlaybackDatePage,
    parse_modern_playback_date_v2_response,
    parse_modern_playback_date_v3_response,
    parse_modern_playback_date_v4_response,
)
from .onboard_playback_modern import (
    PLAYBACK_GET_LIST_V1_COMMAND,
    PLAYBACK_GET_LIST_V2_COMMAND,
    ModernPlaybackPage,
    build_modern_playback_list_v1_request,
    build_modern_playback_list_v2_request,
    parse_modern_playback_list_v1_response,
    parse_modern_playback_list_v2_response,
)
from .onboard_playback_v34 import (
    build_modern_playback_list_v3_request,
    build_modern_playback_list_v4_request,
    parse_modern_playback_list_v3_response,
    parse_modern_playback_list_v4_response,
)
from .wire import finish_mode1, finish_mode2, new_header, randomized_flags

_BUILTIN_DOMAIN = 0


def _command_for_protocol(protocol_version: int) -> int:
    if protocol_version == 1:
        return PLAYBACK_GET_LIST_V1_COMMAND
    if protocol_version in (2, 3, 4):
        return PLAYBACK_GET_LIST_V2_COMMAND
    raise ValueError("only recovered playback-list protocols V1 through V4 are supported")


def _build_list_body(
    query: OnboardRecordingQuery,
    page_index: int,
    protocol_version: int,
) -> bytes:
    builders = {
        1: build_modern_playback_list_v1_request,
        2: build_modern_playback_list_v2_request,
        3: build_modern_playback_list_v3_request,
        4: build_modern_playback_list_v4_request,
    }
    try:
        builder = builders[protocol_version]
    except KeyError as exc:
        raise ValueError(
            "only recovered playback-list protocols V1 through V4 are supported"
        ) from exc
    return builder(query, page_index=page_index)


def build_onboard_playback_list_request(
    node: CertifiedNode,
    access_id: int,
    device_id: int,
    query: OnboardRecordingQuery,
    sequence: int,
    message_id: int,
    request_id: int,
    *,
    page_index: int = 0,
    protocol_version: int = 2,
) -> bytes:
    """Wrap one recovered V1-V4 list body in the native MessageMgr B9 envelope."""

    return _build_onboard_playback_request(
        node,
        access_id,
        device_id,
        sequence,
        message_id,
        request_id,
        command=_command_for_protocol(protocol_version),
        body=_build_list_body(query, page_index, protocol_version),
    )


def build_onboard_playback_date_request(
    node: CertifiedNode,
    access_id: int,
    device_id: int,
    query: OnboardRecordingQuery,
    sequence: int,
    message_id: int,
    request_id: int,
    *,
    page_index: int = 0,
    protocol_version: int = 2,
) -> bytes:
    """Wrap one recovered date-list body in BuiltIn command ``18``."""

    if protocol_version not in (2, 3, 4):
        raise ValueError("only playback-date protocols V2 through V4 are supported")
    return _build_onboard_playback_request(
        node,
        access_id,
        device_id,
        sequence,
        message_id,
        request_id,
        command=PLAYBACK_GET_DATE_LIST_COMMAND,
        body=_build_list_body(query, page_index, protocol_version),
    )


def _build_onboard_playback_request(
    node: CertifiedNode,
    access_id: int,
    device_id: int,
    sequence: int,
    message_id: int,
    request_id: int,
    *,
    command: int,
    body: bytes,
) -> bytes:
    prefix = bytes((_BUILTIN_DOMAIN, command, 0, 0))
    payload = prefix + struct.pack("<I", request_id) + body
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


def parse_onboard_playback_list_response(
    frame: bytes,
    *,
    request_id: int,
    protocol_version: int = 2,
) -> ModernPlaybackPage | None:
    """Parse only a correlated BuiltIn list response and its bounded selected body."""

    body = _extract_correlated_body(
        frame,
        request_id=request_id,
        command=_command_for_protocol(protocol_version),
    )
    if body is None:
        return None
    parsers = {
        1: parse_modern_playback_list_v1_response,
        2: parse_modern_playback_list_v2_response,
        3: parse_modern_playback_list_v3_response,
        4: parse_modern_playback_list_v4_response,
    }
    try:
        return parsers[protocol_version](body)
    except (KeyError, ValueError):
        return None


def parse_onboard_playback_date_response(
    frame: bytes,
    *,
    request_id: int,
    protocol_version: int = 2,
) -> ModernPlaybackDatePage | None:
    """Parse only a correlated BuiltIn command-18 date-list response."""

    body = _extract_correlated_body(
        frame,
        request_id=request_id,
        command=PLAYBACK_GET_DATE_LIST_COMMAND,
    )
    if body is None:
        return None
    parsers = {
        2: parse_modern_playback_date_v2_response,
        3: parse_modern_playback_date_v3_response,
        4: parse_modern_playback_date_v4_response,
    }
    try:
        return parsers[protocol_version](body)
    except (KeyError, ValueError):
        return None


def _extract_correlated_body(
    frame: bytes,
    *,
    request_id: int,
    command: int,
) -> bytes | None:
    if len(frame) < 0x3C or frame[1] != 0xB9:
        return None
    payload_length = struct.unpack_from("<H", frame, 0x30)[0]
    if payload_length < 8 or 0x34 + payload_length > len(frame):
        return None
    payload = frame[0x34 : 0x34 + payload_length]
    if payload[:4] != bytes((_BUILTIN_DOMAIN, command, 0, 0)):
        return None
    if struct.unpack_from("<I", payload, 4)[0] != request_id:
        return None
    return payload[8:]


def build_onboard_playback_receipt(
    node: CertifiedNode,
    response: bytes,
    sequence: int,
) -> bytes:
    """Acknowledge one application B9 response using its original route metadata."""

    if len(response) < 0x34 or response[1] != 0xB9:
        raise ValueError("onboard playback receipt requires a full B9 response")
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
    raise ValueError("onboard playback receipt requires an encrypted response")
