from __future__ import annotations

from datetime import UTC, datetime

import pytest

from backend.app.drivers.contracts import OnboardRecordingQuery
from backend.app.drivers.yoosee.p2p.onboard_playback_message import (
    build_onboard_playback_date_message,
    build_onboard_playback_list_message,
    build_onboard_playback_recording_types_message,
)
from backend.app.drivers.yoosee.p2p.stream_protocol import parse_builtin_command


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
