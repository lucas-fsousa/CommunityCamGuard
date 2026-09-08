from __future__ import annotations

import pytest

from backend.app.drivers.yoosee.p2p.onboard_playback_link import (
    InitialPlaybackLinkMetadata,
    build_initial_playback_link_user_data,
    parse_initial_playback_link_user_data,
)


@pytest.mark.parametrize(
    ("platform_version", "expected_auxiliary"),
    [(1, 0), (2, 0x0300)],
)
def test_initial_playback_link_matches_native_layout(platform_version, expected_auxiliary):
    payload = build_initial_playback_link_user_data(
        1_725_000_000_123_456,
        1_725_000_001_987_654,
        source_id=7,
        camera_id=-1,
        device_platform_version=platform_version,
    )

    assert len(payload) == 32
    assert parse_initial_playback_link_user_data(
        payload,
        device_platform_version=platform_version,
    ) == InitialPlaybackLinkMetadata(
        playback_time_ms=1_725_000_001_987,
        file_start_time_ms=1_725_000_000_123,
        source_id=7,
        rate_milli=1000,
        platform_auxiliary=expected_auxiliary,
        camera_id=255,
    )


def test_initial_playback_link_defaults_selected_time_and_current_app_route():
    payload = build_initial_playback_link_user_data(
        123_999,
        device_platform_version=1,
    )

    parsed = parse_initial_playback_link_user_data(payload, device_platform_version=1)
    assert parsed.playback_time_ms == parsed.file_start_time_ms == 123
    assert parsed.source_id == 0 and parsed.camera_id == 255


@pytest.mark.parametrize("platform_version", [None, True, 0, 3])
def test_playback_link_rejects_unknown_platform(platform_version):
    with pytest.raises(ValueError, match="authoritative platform"):
        build_initial_playback_link_user_data(
            1_000,
            device_platform_version=platform_version,
        )


@pytest.mark.parametrize(
    ("arguments", "message"),
    [
        ({"file_start_time_us": -1}, "file start time"),
        ({"file_start_time_us": 1_000, "playback_time_us": -1}, "selected time"),
        ({"file_start_time_us": 1_000, "source_id": 0x10000}, "source id"),
        ({"file_start_time_us": 1_000, "camera_id": 255}, "camera id"),
    ],
)
def test_playback_link_rejects_out_of_range_fields(arguments, message):
    with pytest.raises(ValueError, match=message):
        build_initial_playback_link_user_data(
            **arguments,
            device_platform_version=1,
        )


def test_playback_link_parser_rejects_non_initial_or_reserved_data():
    payload = bytearray(
        build_initial_playback_link_user_data(1_000, device_platform_version=1)
    )
    payload[26] = 1
    with pytest.raises(ValueError, match="reserved"):
        parse_initial_playback_link_user_data(bytes(payload), device_platform_version=1)

    payload[26] = 0
    payload[19] = 0
    with pytest.raises(ValueError, match="initial rate"):
        parse_initial_playback_link_user_data(bytes(payload), device_platform_version=1)
