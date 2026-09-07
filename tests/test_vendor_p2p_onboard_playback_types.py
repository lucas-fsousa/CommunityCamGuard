from __future__ import annotations

import struct

import pytest

from backend.app.drivers.yoosee.p2p.onboard_playback_types import (
    ModernPlaybackRecordingType,
    ModernPlaybackRecordingTypePage,
    merge_modern_playback_recording_types_v4_fragments,
    parse_modern_playback_recording_types_v3_response,
    parse_modern_playback_recording_types_v4_response,
)


def _v3_response(items: tuple[tuple[int, int, int], ...]) -> bytes:
    body = bytearray(26 + 17 + len(items) * 13)
    body[0] = 3
    struct.pack_into("<iIIIQ", body, 1, -1, 2, 4, len(items), 1_700_000_000_000)
    body[25] = 1
    body[26:43] = b"ignored\0".ljust(17, b"\0")
    for index, (start_offset, duration, flags) in enumerate(items):
        struct.pack_into("<QI", body, 43 + index * 13, start_offset, duration)
        body[55 + index * 13] = flags
    return bytes(body)


def _v4_response(fragment_index: int, items: tuple[tuple[int, int, int], ...]) -> bytes:
    body = bytearray(26 + len(items) * 12)
    body[:3] = bytes((4, 2, fragment_index))
    struct.pack_into("<HHQ", body, 3, 5, 3, 1_700_000_000_000)
    struct.pack_into("<H", body, 24, len(items))
    for index, item in enumerate(items):
        struct.pack_into("<III", body, 26 + index * 12, *item)
    return bytes(body)


def test_parses_v3_recording_type_windows_and_low_flag_bit():
    assert parse_modern_playback_recording_types_v3_response(
        _v3_response(((1_000, 30_000, 6), (40_000, 20_000, 3)))
    ) == ModernPlaybackRecordingTypePage(
        2,
        4,
        -1,
        (
            ModernPlaybackRecordingType(1_700_000_001_000, 1_700_000_031_000, 30_000, 0),
            ModernPlaybackRecordingType(1_700_000_040_000, 1_700_000_060_000, 20_000, 1),
        ),
    )


def test_merges_complete_v4_recording_type_fragments_in_wire_order():
    second = parse_modern_playback_recording_types_v4_response(
        _v4_response(1, ((40_000, 20_000, 2),))
    )
    first = parse_modern_playback_recording_types_v4_response(
        _v4_response(0, ((1_000, 30_000, 1),))
    )

    assert merge_modern_playback_recording_types_v4_fragments(
        (second, first)
    ) == ModernPlaybackRecordingTypePage(
        3,
        5,
        None,
        (
            ModernPlaybackRecordingType(1_700_000_001_000, 1_700_000_031_000, 30_000, 1),
            ModernPlaybackRecordingType(1_700_000_040_000, 1_700_000_060_000, 20_000, 0),
        ),
        1,
        2,
    )


def test_v3_rejects_zero_duration():
    with pytest.raises(ValueError, match="item is invalid"):
        parse_modern_playback_recording_types_v3_response(_v3_response(((0, 0, 1),)))


def test_v4_rejects_truncated_item():
    with pytest.raises(ValueError, match="size is inconsistent"):
        parse_modern_playback_recording_types_v4_response(
            _v4_response(0, ((0, 1, 1),))[:-1]
        )


def test_v4_rejects_incomplete_fragment_set():
    first = parse_modern_playback_recording_types_v4_response(
        _v4_response(0, ((0, 1, 1),))
    )

    with pytest.raises(ValueError, match="incomplete"):
        merge_modern_playback_recording_types_v4_fragments((first,))
