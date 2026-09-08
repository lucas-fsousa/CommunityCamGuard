from __future__ import annotations

import struct
from datetime import UTC, datetime

import pytest

from backend.app.drivers.contracts import OnboardRecordingQuery
from backend.app.drivers.yoosee.p2p.onboard_playback_dates import ModernPlaybackDatePage
from backend.app.drivers.yoosee.p2p.onboard_playback_message import (
    build_onboard_playback_date_message,
    build_onboard_playback_list_message,
    build_onboard_playback_recording_types_message,
)
from backend.app.drivers.yoosee.p2p.onboard_playback_modern import ModernPlaybackPage
from backend.app.drivers.yoosee.p2p.onboard_playback_response import (
    parse_onboard_playback_date_response,
    parse_onboard_playback_list_response,
    parse_onboard_playback_recording_types_response,
)
from backend.app.drivers.yoosee.p2p.onboard_playback_types import (
    ModernPlaybackRecordingTypePage,
)
from backend.app.drivers.yoosee.p2p.stream_protocol import (
    build_builtin_command,
    parse_builtin_command,
)


def _query() -> OnboardRecordingQuery:
    return OnboardRecordingQuery(
        datetime(2026, 9, 1, 12, 0, tzinfo=UTC),
        datetime(2026, 9, 1, 13, 0, tzinfo=UTC),
        limit=50,
    )


@pytest.mark.parametrize(
    ("protocol_version", "command"),
    ((1, 0), (2, 16), (3, 16), (4, 16)),
)
def test_builds_transport_neutral_playback_list_message(protocol_version: int, command: int):
    message = parse_builtin_command(
        build_onboard_playback_list_message(
            _query(),
            0xDEADBEEF,
            page_index=3,
            protocol_version=protocol_version,
        )
    )

    assert message.command == command
    assert message.flags == 0
    assert message.timestamp == 0xDEADBEEF
    assert message.payload[0] == protocol_version


@pytest.mark.parametrize("protocol_version", (2, 3, 4))
def test_builds_transport_neutral_date_message(protocol_version: int):
    message = parse_builtin_command(
        build_onboard_playback_date_message(
            _query(),
            20,
            page_index=3,
            protocol_version=protocol_version,
        )
    )

    assert message.command == 18
    assert message.timestamp == 20
    assert message.payload[0] == protocol_version


@pytest.mark.parametrize("protocol_version", (3, 4))
def test_builds_transport_neutral_recording_type_message(protocol_version: int):
    message = parse_builtin_command(
        build_onboard_playback_recording_types_message(
            _query(),
            20,
            page_index=3,
            protocol_version=protocol_version,
        )
    )

    assert message.command == 15
    assert message.timestamp == 20
    assert message.payload[0] == protocol_version


def test_rejects_unrecovered_protocol_versions():
    with pytest.raises(ValueError):
        build_onboard_playback_list_message(_query(), 1, protocol_version=5)
    with pytest.raises(ValueError):
        build_onboard_playback_date_message(_query(), 1, protocol_version=1)
    with pytest.raises(ValueError):
        build_onboard_playback_recording_types_message(_query(), 1, protocol_version=2)


def _empty_v23_response(protocol_version: int) -> bytes:
    body = bytearray(26)
    body[0] = protocol_version
    struct.pack_into("<iIIIQ", body, 1, -1, 0, 0, 0, 1_788_264_000_000)
    return bytes(body)


def test_dispatches_correlated_response_without_requiring_command_echo():
    response = build_builtin_command(0, _empty_v23_response(2), timestamp_us=0x12345678)

    assert parse_onboard_playback_list_response(response, 0x12345678) == ModernPlaybackPage(
        0,
        0,
        -1,
        (),
    )


def test_dispatches_date_and_recording_type_responses_by_inner_request_id():
    date_response = build_builtin_command(0, _empty_v23_response(2), timestamp_us=11)
    type_response = build_builtin_command(0, _empty_v23_response(3), timestamp_us=12)

    assert parse_onboard_playback_date_response(date_response, 11) == ModernPlaybackDatePage(
        0,
        0,
        -1,
        (),
    )
    assert parse_onboard_playback_recording_types_response(
        type_response,
        12,
    ) == ModernPlaybackRecordingTypePage(0, 0, -1, ())


@pytest.mark.parametrize(
    "parser",
    (
        parse_onboard_playback_list_response,
        parse_onboard_playback_date_response,
        parse_onboard_playback_recording_types_response,
    ),
)
def test_rejects_response_with_different_inner_request_id(parser):
    response = build_builtin_command(0, _empty_v23_response(3), timestamp_us=7)

    with pytest.raises(ValueError, match="request ID does not match"):
        parser(response, 8, protocol_version=3)


def test_rejects_sdk_error_response_before_operation_parser():
    response = build_builtin_command(0xFF, bytes(8), timestamp_us=7)

    with pytest.raises(ValueError, match="SDK error response"):
        parse_onboard_playback_list_response(response, 7)


@pytest.mark.parametrize("request_id", (-1, 0x1_0000_0000, True))
def test_rejects_invalid_response_request_id(request_id):
    response = build_builtin_command(0, _empty_v23_response(2), timestamp_us=7)

    with pytest.raises(ValueError, match="request ID is invalid"):
        parse_onboard_playback_list_response(response, request_id)
