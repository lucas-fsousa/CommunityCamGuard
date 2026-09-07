from datetime import UTC, datetime

import pytest

from backend.app.drivers.contracts import OnboardRecordingQuery
from backend.app.drivers.yoosee.p2p.onboard_playback_modern import (
    ModernPlaybackFile,
    ModernPlaybackPage,
)
from backend.app.drivers.yoosee.p2p.onboard_playback_v34 import (
    build_modern_playback_list_v3_request,
    build_modern_playback_list_v4_request,
    merge_modern_playback_v4_fragments,
    parse_modern_playback_list_v3_response,
    parse_modern_playback_list_v4_response,
)


def _query(limit: int = 50) -> OnboardRecordingQuery:
    return OnboardRecordingQuery(
        datetime(2026, 9, 1, 12, 0, tzinfo=UTC),
        datetime(2026, 9, 1, 13, 0, tzinfo=UTC),
        limit=limit,
    )


def test_builds_recovered_v3_request_with_low_rec_opts_byte_and_ordering_flag():
    payload = build_modern_playback_list_v3_request(
        _query(), page_index=3, rec_opts=0x15A, ascending=True
    )

    assert payload.hex() == (
        "03"
        "002ad75ca0010000"
        "80180e5da0010000"
        "00000003"
        "00000032"
        "5a0000000000000000000000000000000001"
    )


def test_builds_recovered_v4_request_with_options_camera_and_ordering():
    payload = build_modern_playback_list_v4_request(
        _query(),
        page_index=3,
        rec_opts=0x12345678,
        camera_id=-1,
        ascending=True,
    )

    assert payload.hex() == (
        "04"
        "002ad75ca0010000"
        "80180e5da0010000"
        "00000003"
        "00000032"
        "78563412ff00000000000000000000000001"
    )


def test_v3_v4_request_builders_fail_closed_on_invalid_options():
    with pytest.raises(ValueError):
        build_modern_playback_list_v3_request(_query(), rec_opts=-1)
    with pytest.raises(ValueError):
        build_modern_playback_list_v4_request(_query(), camera_id=128)
    with pytest.raises(ValueError):
        build_modern_playback_list_v4_request(_query(), ascending=1)  # type: ignore[arg-type]


def _v3_response() -> bytes:
    header = bytearray(26)
    header[0] = 3
    header[1:5] = (-1).to_bytes(4, "little", signed=True)
    header[5:9] = (3).to_bytes(4, "little")
    header[9:13] = (5).to_bytes(4, "little")
    header[13:17] = (2).to_bytes(4, "little")
    header[17:25] = (1_788_264_000_000).to_bytes(8, "little")
    header[25] = 2
    types = b"motion\0".ljust(17, b"\0") + b"scheduled\0".ljust(17, b"\0")
    items = (
        (1_000).to_bytes(8, "little")
        + (30_000).to_bytes(4, "little")
        + bytes([0])
        + (0x1_0000_0000).to_bytes(8, "little")
        + (60_000).to_bytes(4, "little")
        + bytes([1])
    )
    return bytes(header) + types + items


def test_parses_v3_64_bit_offsets_and_type_table():
    assert parse_modern_playback_list_v3_response(_v3_response()) == ModernPlaybackPage(
        page_index=3,
        total_pages=5,
        marker=-1,
        items=(
            ModernPlaybackFile(1_788_264_001_000, 1_788_264_031_000, 30_000, "motion"),
            ModernPlaybackFile(
                1_792_558_967_296,
                1_792_559_027_296,
                60_000,
                "scheduled",
            ),
        ),
    )


def _v4_response(fragment_index: int, start_offset_ms: int, flags: int) -> bytes:
    header = bytearray(26)
    header[0] = 4
    header[1] = 2
    header[2] = fragment_index
    header[3:5] = (5).to_bytes(2, "little")
    header[5:7] = (3).to_bytes(2, "little")
    header[7:15] = (1_788_264_000_000).to_bytes(8, "little")
    header[24:26] = (1).to_bytes(2, "little")
    item = (
        start_offset_ms.to_bytes(4, "little")
        + (30_000).to_bytes(4, "little")
        + flags.to_bytes(4, "little")
    )
    return bytes(header) + item


def test_parses_and_merges_complete_v4_fragment_set():
    first = parse_modern_playback_list_v4_response(_v4_response(0, 1_000, 4))
    second = parse_modern_playback_list_v4_response(_v4_response(1, 40_000, 5))

    assert first.fragment_index == 0
    assert first.fragment_count == 2
    assert merge_modern_playback_v4_fragments((second, first)) == ModernPlaybackPage(
        page_index=3,
        total_pages=5,
        marker=None,
        items=(
            ModernPlaybackFile(1_788_264_001_000, 1_788_264_031_000, 30_000, "0"),
            ModernPlaybackFile(1_788_264_040_000, 1_788_264_070_000, 30_000, "1"),
        ),
        fragment_index=1,
        fragment_count=2,
    )


def test_v3_v4_response_parsers_reject_bad_sizes_indices_and_incomplete_fragments():
    with pytest.raises(ValueError):
        parse_modern_playback_list_v3_response(_v3_response()[:-1])
    invalid_fragment = bytearray(_v4_response(0, 1, 0))
    invalid_fragment[2] = 2
    with pytest.raises(ValueError):
        parse_modern_playback_list_v4_response(bytes(invalid_fragment))
    first = parse_modern_playback_list_v4_response(_v4_response(0, 1, 0))
    with pytest.raises(ValueError):
        merge_modern_playback_v4_fragments((first,))
