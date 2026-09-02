from datetime import UTC, datetime, timedelta, timezone

import pytest

from backend.app.drivers.yoosee.p2p.onboard_playback_legacy import (
    build_legacy_recording_list_request,
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
