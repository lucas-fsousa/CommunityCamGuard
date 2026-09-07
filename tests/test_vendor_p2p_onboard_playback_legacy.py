from datetime import UTC, datetime, timedelta, timezone

import pytest

from backend.app.drivers.yoosee.p2p.onboard_playback_legacy import (
    build_legacy_recording_list_request,
    parse_legacy_recording_filename,
    unpack_legacy_recording_list_request,
)


def test_builds_exact_jni_recovered_16_byte_layout_in_camera_local_time():
    payload = build_legacy_recording_list_request(
        datetime(2026, 9, 1, 12, 34, 45, tzinfo=UTC),
        datetime(2026, 9, 1, 13, 35, 1, tzinfo=UTC),
        camera_timezone=timezone(timedelta(hours=-3)),
    )

    assert payload == bytes.fromhex("03 01 00 00 ea 07 09 01 09 22 ea 07 09 01 0a 24")
    assert unpack_legacy_recording_list_request(payload) == (
        datetime(2026, 9, 1, 9, 34),
        datetime(2026, 9, 1, 10, 36),
    )


def test_minute_aligned_end_is_not_expanded():
    payload = build_legacy_recording_list_request(
        datetime(2026, 9, 1, 12, 0, tzinfo=UTC),
        datetime(2026, 9, 1, 13, 0, tzinfo=UTC),
        camera_timezone=UTC,
    )

    assert unpack_legacy_recording_list_request(payload)[1] == datetime(2026, 9, 1, 13, 0)


def test_rejects_non_utc_or_invalid_windows_and_payloads():
    end = datetime(2026, 9, 1, 13, 0, tzinfo=UTC)
    with pytest.raises(ValueError):
        build_legacy_recording_list_request(
            datetime(2026, 9, 1, 12, 0), end, camera_timezone=UTC
        )
    with pytest.raises(ValueError):
        build_legacy_recording_list_request(end, end, camera_timezone=UTC)
    with pytest.raises(ValueError):
        unpack_legacy_recording_list_request(bytes(15))
    with pytest.raises(ValueError):
        unpack_legacy_recording_list_request(bytes(16))


def test_parses_apk_legacy_filename_in_explicit_camera_timezone():
    item = parse_legacy_recording_filename(
        "disc1/2026-09-07_12:34:56_M.mp4(60s)",
        camera_timezone=timezone(timedelta(hours=-3)),
    )

    assert item.filename == "disc1/2026-09-07_12:34:56_M.mp4(60s)"
    assert item.start_utc == datetime(2026, 9, 7, 15, 34, 56, tzinfo=UTC)
    assert item.end_utc == datetime(2026, 9, 7, 15, 35, 56, tzinfo=UTC)
    assert item.duration_seconds == 60
    assert item.recording_type == "M"


@pytest.mark.parametrize(
    "filename",
    [
        "short",
        "disc1/2026-09-07_12:34:56_X.mp4(60s)",
        "disc1/2026-09-07_12:34:56_M.mp4(xs)",
        "disc1/2026-09-07_12:34:56_M.mp4(0s)",
        "disc1/2026-09-07_12:34:56_M.mp4(60s)junk",
        "disc1/2026-09-07_12:34:56_M.mp4(60s)|other",
    ],
)
def test_rejects_malformed_legacy_filenames(filename):
    with pytest.raises(ValueError):
        parse_legacy_recording_filename(filename, camera_timezone=UTC)


def test_rejects_ambiguous_camera_wall_time():
    from zoneinfo import ZoneInfo

    with pytest.raises(ValueError, match="timestamp is invalid"):
        parse_legacy_recording_filename(
            "disc1/2026-11-01_01:30:00_A.mp4(10s)",
            camera_timezone=ZoneInfo("America/New_York"),
        )
