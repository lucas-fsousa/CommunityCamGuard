"""Transport-neutral response dispatch for Yoosee onboard-playback reads."""

from __future__ import annotations

from collections.abc import Callable
from typing import TypeVar

from .onboard_playback_dates import (
    ModernPlaybackDatePage,
    parse_modern_playback_date_v2_response,
    parse_modern_playback_date_v3_response,
    parse_modern_playback_date_v4_response,
)
from .onboard_playback_modern import (
    ModernPlaybackPage,
    parse_modern_playback_list_v1_response,
    parse_modern_playback_list_v2_response,
)
from .onboard_playback_types import (
    ModernPlaybackRecordingTypePage,
    parse_modern_playback_recording_types_v3_response,
    parse_modern_playback_recording_types_v4_response,
)
from .onboard_playback_v34 import (
    parse_modern_playback_list_v3_response,
    parse_modern_playback_list_v4_response,
)
from .stream_protocol import parse_builtin_command

_ResponseT = TypeVar("_ResponseT")


class OnboardPlaybackSDKError(ValueError):
    """Structured BuiltIn error returned by the IoTVideo message manager."""

    def __init__(self, error_code: int) -> None:
        self.error_code = error_code
        super().__init__(f"onboard playback returned SDK error {error_code}")


def _parse_correlated(
    message: bytes,
    request_id: int,
    protocol_version: int,
    parsers: dict[int, Callable[[bytes], _ResponseT]],
) -> _ResponseT:
    if type(request_id) is not int or not 0 <= request_id <= 0xFFFFFFFF:
        raise ValueError("onboard playback request ID is invalid")
    response = parse_builtin_command(message)
    if response.timestamp != request_id:
        raise ValueError("onboard playback response request ID does not match")
    if response.command == 0xFF:
        if len(response.payload) != 8:
            raise ValueError("onboard playback SDK error payload size is invalid")
        # MessageMgr::_rcv_passthrough_cb masks the first native u32 to 16 bits
        # before mapping it to the public ErrorInfo object. The second u32 is
        # retained as opaque/reserved until an authoritative use is recovered.
        raise OnboardPlaybackSDKError(
            int.from_bytes(response.payload[:4], "little") & 0xFFFF
        )
    try:
        parser = parsers[protocol_version]
    except KeyError as exc:
        raise ValueError("onboard playback response protocol is unsupported") from exc
    return parser(response.payload)


def parse_onboard_playback_list_response(
    message: bytes,
    request_id: int,
    *,
    protocol_version: int = 2,
) -> ModernPlaybackPage:
    """Parse a correlated list response after the SDK has removed its B9 carrier."""

    return _parse_correlated(
        message,
        request_id,
        protocol_version,
        {
            1: parse_modern_playback_list_v1_response,
            2: parse_modern_playback_list_v2_response,
            3: parse_modern_playback_list_v3_response,
            4: parse_modern_playback_list_v4_response,
        },
    )


def parse_onboard_playback_date_response(
    message: bytes,
    request_id: int,
    *,
    protocol_version: int = 2,
) -> ModernPlaybackDatePage:
    """Parse a correlated command-18 date response without a B9 dependency."""

    return _parse_correlated(
        message,
        request_id,
        protocol_version,
        {
            2: parse_modern_playback_date_v2_response,
            3: parse_modern_playback_date_v3_response,
            4: parse_modern_playback_date_v4_response,
        },
    )


def parse_onboard_playback_recording_types_response(
    message: bytes,
    request_id: int,
    *,
    protocol_version: int = 3,
) -> ModernPlaybackRecordingTypePage:
    """Parse a correlated command-15 recording-type response."""

    return _parse_correlated(
        message,
        request_id,
        protocol_version,
        {
            3: parse_modern_playback_recording_types_v3_response,
            4: parse_modern_playback_recording_types_v4_response,
        },
    )
