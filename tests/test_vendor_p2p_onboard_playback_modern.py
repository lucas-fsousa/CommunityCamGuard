from datetime import UTC, datetime

import pytest

from backend.app.drivers.contracts import OnboardRecordingQuery
from backend.app.drivers.yoosee.p2p.onboard_playback_modern import (
    PLAYBACK_GET_LIST_V1_COMMAND,
    PLAYBACK_GET_LIST_V2_COMMAND,
    ModernPlaybackFile,
    ModernPlaybackPage,
    build_modern_playback_list_v1_request,
    build_modern_playback_list_v2_request,
    parse_modern_playback_list_v1_response,
    parse_modern_playback_list_v2_response,
    unpack_modern_playback_list_v2_request,
)


def _query(limit: int = 50) -> OnboardRecordingQuery:
    return OnboardRecordingQuery(
        datetime(2026, 9, 1, 12, 0, tzinfo=UTC),
        datetime(2026, 9, 1, 13, 0, tzinfo=UTC),
        limit=limit,
    )


def test_builds_recovered_v2_payload_for_builtin_command_16():
    payload = build_modern_playback_list_v2_request(_query(), page_index=3, filter_type="motion")

    assert PLAYBACK_GET_LIST_V2_COMMAND == 16
    assert len(payload) == 43
    assert payload.hex() == (
        "02"
        "002ad75ca0010000"
        "80180e5da0010000"
        "00000003"
        "00000032"
        "6d6f74696f6e000000000000000000000000"
    )
    assert unpack_modern_playback_list_v2_request(payload) == {
        "start_ms": 1_788_264_000_000,
        "end_ms": 1_788_267_600_000,
        "page_index": 3,
        "count_per_page": 50,
        "filter_type": "motion",
    }


def test_builds_recovered_v1_payload_for_builtin_command_zero():
    payload = build_modern_playback_list_v1_request(_query(), page_index=3)

    assert PLAYBACK_GET_LIST_V1_COMMAND == 0
    assert len(payload) == 43
    assert payload[:25].hex() == (
        "01"
        "002ad75ca0010000"
        "80180e5da0010000"
        "00000003"
        "00000032"
    )
    assert payload[25:] == bytes(18)


def test_rejects_unrecovered_or_unsafe_v2_fields():
    with pytest.raises(ValueError):
        build_modern_playback_list_v2_request(_query(), page_index=-1)
    with pytest.raises(ValueError):
        build_modern_playback_list_v2_request(_query(), filter_type="áudio")
    with pytest.raises(ValueError):
        build_modern_playback_list_v2_request(_query(), filter_type="x" * 18)
    with pytest.raises(ValueError):
        unpack_modern_playback_list_v2_request(bytes(43))


def test_decoder_rejects_nonzero_reserved_filter_tail():
    payload = bytearray(build_modern_playback_list_v2_request(_query(), filter_type="A"))
    payload[-1] = 1

    with pytest.raises(ValueError):
        unpack_modern_playback_list_v2_request(bytes(payload))


def _v2_response() -> bytes:
    header = bytearray(26)
    header[0] = 2
    header[1:5] = (-1).to_bytes(4, "little", signed=True)
    header[5:9] = (3).to_bytes(4, "little")
    header[9:13] = (5).to_bytes(4, "little")
    header[13:17] = (2).to_bytes(4, "little")
    header[17:25] = (1_788_264_000_000).to_bytes(8, "little")
    header[25] = 2
    types = b"motion\0".ljust(17, b"\0") + b"scheduled\0".ljust(17, b"\0")
    items = (
        (1_000).to_bytes(4, "little")
        + (30_000).to_bytes(4, "little")
        + bytes([0])
        + (40_000).to_bytes(4, "little")
        + (60_000).to_bytes(4, "little")
        + bytes([1])
    )
    return bytes(header) + types + items


def test_parses_recovered_v2_response_header_types_and_nine_byte_items():
    assert parse_modern_playback_list_v2_response(_v2_response()) == ModernPlaybackPage(
        page_index=3,
        total_pages=5,
        marker=-1,
        items=(
            ModernPlaybackFile(
                1_788_264_001_000, 1_788_264_031_000, 30_000, "motion"
            ),
            ModernPlaybackFile(
                1_788_264_040_000, 1_788_264_100_000, 60_000, "scheduled"
            ),
        ),
    )


def test_v2_response_parser_fails_closed_on_bounds_types_and_duration():
    with pytest.raises(ValueError):
        parse_modern_playback_list_v2_response(_v2_response()[:-1])

    unknown_type = bytearray(_v2_response())
    unknown_type[-1] = 2
    with pytest.raises(ValueError):
        parse_modern_playback_list_v2_response(bytes(unknown_type))

    zero_duration = bytearray(_v2_response())
    first_item_offset = 26 + 2 * 17
    zero_duration[first_item_offset + 4 : first_item_offset + 8] = bytes(4)
    with pytest.raises(ValueError):
        parse_modern_playback_list_v2_response(bytes(zero_duration))


def _v1_response() -> bytes:
    header = (
        bytes([1])
        + (3).to_bytes(4, "little")
        + (5).to_bytes(4, "little")
        + (1).to_bytes(4, "little")
    )
    item = (
        (1_788_264_001_000).to_bytes(8, "little")
        + (1_788_264_031_000).to_bytes(8, "little")
        + b"motion\0".ljust(17, b"\0")
    )
    return header + item


def test_parses_recovered_v1_header_and_fixed_items():
    assert parse_modern_playback_list_v1_response(_v1_response()) == ModernPlaybackPage(
        page_index=3,
        total_pages=5,
        marker=None,
        items=(
            ModernPlaybackFile(1_788_264_001_000, 1_788_264_031_000, 30_000, "motion"),
        ),
    )


def test_v1_response_parser_fails_closed_on_size_and_time_range():
    with pytest.raises(ValueError):
        parse_modern_playback_list_v1_response(_v1_response()[:-1])
    invalid_range = bytearray(_v1_response())
    invalid_range[13:21] = (1_788_264_032_000).to_bytes(8, "little")
    with pytest.raises(ValueError):
        parse_modern_playback_list_v1_response(bytes(invalid_range))
