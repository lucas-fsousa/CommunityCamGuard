from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from backend.app.drivers.contracts import OnboardRecordingQuery
from backend.app.drivers.yoosee.p2p.onboard_playback_protocol import (
    select_onboard_playback_protocol,
)


def _query(duration: timedelta) -> OnboardRecordingQuery:
    start = datetime(2026, 7, 1, tzinfo=UTC)
    return OnboardRecordingQuery(start, start + duration)


def test_native_selector_keeps_short_default_query_on_v2():
    assert select_onboard_playback_protocol(
        _query(timedelta(days=1)), requested_version=2, minimum_version=2
    ) == 2


def test_native_selector_promotes_exact_long_window_boundary_to_v3():
    below = timedelta(microseconds=(125 << 35) - 1)
    boundary = timedelta(microseconds=125 << 35)

    assert select_onboard_playback_protocol(
        _query(below), requested_version=2, minimum_version=2
    ) == 2
    assert select_onboard_playback_protocol(
        _query(boundary), requested_version=2, minimum_version=2
    ) == 3


def test_native_selector_does_not_promote_long_ascending_query_to_v3():
    assert select_onboard_playback_protocol(
        _query(timedelta(days=90)),
        requested_version=2,
        minimum_version=2,
        ascending_order=True,
    ) == 2


def test_native_selector_enforces_operation_minimum_and_proven_platform_v4():
    query = _query(timedelta(hours=1))

    assert select_onboard_playback_protocol(
        query, requested_version=1, minimum_version=2
    ) == 2
    assert select_onboard_playback_protocol(
        query, requested_version=2, minimum_version=3
    ) == 3
    assert select_onboard_playback_protocol(
        query,
        requested_version=1,
        minimum_version=2,
        device_platform_version=2,
    ) == 4


@pytest.mark.parametrize(
    ("field", "value"),
    (("requested_version", 0), ("minimum_version", 5), ("device_platform_version", -1)),
)
def test_native_selector_rejects_invalid_version_metadata(field: str, value: int):
    arguments: dict[str, int | None] = {
        "requested_version": 2,
        "minimum_version": 2,
        "device_platform_version": None,
    }
    arguments[field] = value

    with pytest.raises(ValueError):
        select_onboard_playback_protocol(_query(timedelta(hours=1)), **arguments)
