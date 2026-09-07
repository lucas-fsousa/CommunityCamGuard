from datetime import UTC, datetime, timedelta, timezone

import pytest

from backend.app.drivers.yoosee.p2p.onboard_playback_legacy import (
    build_legacy_recording_list_request,
    can_use_legacy_playback_manager,
    legacy_manager_accepts_decimal,
    parse_legacy_recording_filename,
    parse_legacy_recording_list_payload,
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


@pytest.mark.parametrize("value", ["0", "+1", "-1", "2147483647", "-2147483648"])
def test_accepts_only_decimal_values_carried_by_java_signed_int(value):
    assert legacy_manager_accepts_decimal(value)


@pytest.mark.parametrize(
    "value", ["", " 1", "1 ", "1.0", "\u0661", "2147483648", "-2147483649", "camera-1"]
)
def test_rejects_values_the_apk_legacy_manager_cannot_carry(value):
    assert not legacy_manager_accepts_decimal(value)


def test_camera3_cannot_use_legacy_manager_without_a_proven_alias():
    assert not can_use_legacy_playback_manager("7443576841", "123")
    assert can_use_legacy_playback_manager("123456789", "123")


def test_parses_apk_legacy_filename_in_explicit_camera_timezone():
    item = parse_legacy_recording_filename(
        "disc1/2026-09-07_12:34:56_M.av (60S)",
        camera_timezone=timezone(timedelta(hours=-3)),
    )

    assert item.filename == "disc1/2026-09-07_12:34:56_M.av (60S)"
    assert item.disc == 1
    assert item.start_utc == datetime(2026, 9, 7, 15, 34, 56, tzinfo=UTC)
    assert item.end_utc == datetime(2026, 9, 7, 15, 35, 56, tzinfo=UTC)
    assert item.duration_seconds == 60
    assert item.recording_type == "M"


@pytest.mark.parametrize(
    "filename",
    [
        "short",
        "disc1/2026-09-07_12:34:56_X.av (60S)",
        "disc1/2026-09-07_12:34:56_M.av (xS)",
        "disc1/2026-09-07_12:34:56_M.av (0S)",
        "disc1/2026-09-07_12:34:56_M.mp4 (60S)",
        "disc1/2026-09-07_12:34:56_M.av (60S)junk",
        "disc1/2026-09-07_12:34:56_M.av (60S)|other",
    ],
)
def test_rejects_malformed_legacy_filenames(filename):
    with pytest.raises(ValueError):
        parse_legacy_recording_filename(filename, camera_timezone=UTC)


def test_rejects_ambiguous_camera_wall_time():
    from zoneinfo import ZoneInfo

    with pytest.raises(ValueError, match="timestamp is invalid"):
        parse_legacy_recording_filename(
            "disc1/2026-11-01_01:30:00_A.av (10S)",
            camera_timezone=ZoneInfo("America/New_York"),
        )


def test_parses_exact_native_legacy_response_layout_with_durations():
    payload = bytes.fromhex(
        "04 01 00 02 "
        "ea 07 19 07 0c 22 38 4d "
        "ea 07 19 07 0c 23 38 41 "
        "3c 00 1e 00"
    )

    page = parse_legacy_recording_list_payload(payload, camera_timezone=UTC)

    assert (page.command, page.option0, page.option1) == (4, 1, 0)
    assert [item.filename for item in page.items] == [
        "disc1/2026-09-07_12:34:56_M.av (60S)",
        "disc1/2026-09-07_12:35:56_A.av (30S)",
    ]
    assert page.items[1].end_utc == datetime(2026, 9, 7, 12, 36, 26, tzinfo=UTC)


def test_parses_native_variant_without_duration_but_does_not_invent_end_time():
    payload = bytes.fromhex("04 00 00 01 ea 07 19 07 0c 22 38 53")

    page = parse_legacy_recording_list_payload(payload, camera_timezone=UTC)

    assert page.items[0].filename == "disc1/2026-09-07_12:34:56_S.av"
    assert page.items[0].duration_seconds is None
    assert page.items[0].end_utc is None


def test_accepts_full_four_bit_native_disc_number():
    item = parse_legacy_recording_filename(
        "disc15/2026-09-07_12:34:56_V.av (1S)", camera_timezone=UTC
    )

    assert item.disc == 15


@pytest.mark.parametrize(
    "payload",
    [
        b"\x03\x01\x00",
        b"\x03\x00\x00\x00",
        b"\x04\x00\x00\x81" + bytes(0x81 * 8),
        b"\x04\x01\x00\x01" + bytes(8),
        bytes.fromhex("04 00 00 01 ea 07 19 07 0c 22 38 58"),
    ],
)
def test_rejects_malformed_native_legacy_response(payload):
    with pytest.raises(ValueError):
        parse_legacy_recording_list_payload(payload, camera_timezone=UTC)
