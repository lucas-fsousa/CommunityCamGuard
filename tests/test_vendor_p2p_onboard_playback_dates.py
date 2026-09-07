from __future__ import annotations

import struct

import pytest

from backend.app.drivers.yoosee.p2p.onboard_playback_dates import (
    ModernPlaybackDatePage,
    merge_modern_playback_date_v4_fragments,
    parse_modern_playback_date_v2_response,
    parse_modern_playback_date_v3_response,
    parse_modern_playback_date_v4_response,
)


def _v23_response(protocol: int, offsets: tuple[int, ...]) -> bytes:
    item_size = 9 if protocol == 2 else 13
    body = bytearray(26 + 17 + len(offsets) * item_size)
    body[0] = protocol
    struct.pack_into("<iIIIQ", body, 1, -1, 2, 4, len(offsets), 1_700_000_000_000)
    body[25] = 1
    body[26:43] = b"schedule\0".ljust(17, b"\0")
    for index, value in enumerate(offsets):
        offset = 43 + index * item_size
        struct.pack_into("<I" if protocol == 2 else "<Q", body, offset, value)
    return bytes(body)


def _v4_response(fragment_index: int, offsets: tuple[int, ...]) -> bytes:
    body = bytearray(26 + len(offsets) * 12)
    body[:3] = bytes((4, 2, fragment_index))
    struct.pack_into("<HHQ", body, 3, 5, 3, 1_700_000_000_000)
    struct.pack_into("<H", body, 24, len(offsets))
    for index, value in enumerate(offsets):
        struct.pack_into("<III", body, 26 + index * 12, value, 123, 1)
    return bytes(body)


def test_parses_v2_date_offsets_and_ignores_non_date_item_fields():
    assert parse_modern_playback_date_v2_response(_v23_response(2, (0, 86_400_000))) == (
        ModernPlaybackDatePage(2, 4, -1, (1_700_000_000_000, 1_700_086_400_000))
    )


def test_parses_v3_wide_date_offsets():
    assert parse_modern_playback_date_v3_response(_v23_response(3, (0x1_0000_0000,))) == (
        ModernPlaybackDatePage(2, 4, -1, (1_704_294_967_296,))
    )


def test_merges_complete_v4_date_fragments_in_wire_order():
    second = parse_modern_playback_date_v4_response(_v4_response(1, (86_400_000,)))
    first = parse_modern_playback_date_v4_response(_v4_response(0, (0,)))

    assert merge_modern_playback_date_v4_fragments((second, first)) == ModernPlaybackDatePage(
        3,
        5,
        None,
        (1_700_000_000_000, 1_700_086_400_000),
        1,
        2,
    )


@pytest.mark.parametrize("parser", [parse_modern_playback_date_v2_response, parse_modern_playback_date_v3_response])
def test_v23_rejects_truncated_payload(parser):
    with pytest.raises(ValueError, match="size is inconsistent"):
        parser(_v23_response(2 if parser is parse_modern_playback_date_v2_response else 3, (0,))[:-1])


def test_v4_rejects_invalid_fragment_metadata():
    body = bytearray(_v4_response(0, (0,)))
    body[1] = 0

    with pytest.raises(ValueError, match="fragment metadata"):
        parse_modern_playback_date_v4_response(bytes(body))


def test_v4_rejects_missing_fragment():
    first = parse_modern_playback_date_v4_response(_v4_response(0, (0,)))

    with pytest.raises(ValueError, match="incomplete"):
        merge_modern_playback_date_v4_fragments((first,))
