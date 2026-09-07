"""Bounded codecs for IoTVideo BuiltIn command 15 (onboard recording types)."""

from __future__ import annotations

import struct
from dataclasses import dataclass

PLAYBACK_GET_RECORDING_TYPES_COMMAND = 15
_V3_HEADER_SIZE = 26
_V3_TYPE_SIZE = 17
_V3_ITEM_SIZE = 13
_V4_HEADER_SIZE = 26
_V4_ITEM_SIZE = 12
_MAX_ITEMS = 200
_MAX_TYPES = 64


@dataclass(frozen=True, slots=True)
class ModernPlaybackRecordingType:
    start_ms: int
    end_ms: int
    duration_ms: int
    type_code: int


@dataclass(frozen=True, slots=True)
class ModernPlaybackRecordingTypePage:
    page_index: int
    total_pages: int
    marker: int | None
    items: tuple[ModernPlaybackRecordingType, ...]
    fragment_index: int | None = None
    fragment_count: int | None = None


def parse_modern_playback_recording_types_v3_response(
    payload: bytes,
) -> ModernPlaybackRecordingTypePage:
    """Parse V3 type windows; the native parser exposes only each item's low flag bit."""

    if len(payload) < _V3_HEADER_SIZE or payload[0] != 3:
        raise ValueError("modern playback recording-types V3 response is invalid")
    marker = struct.unpack_from("<i", payload, 1)[0]
    page_index, total_pages = struct.unpack_from("<II", payload, 5)
    item_count = struct.unpack_from("<I", payload, 13)[0]
    base_time_ms = struct.unpack_from("<Q", payload, 17)[0]
    type_count = payload[25]
    if item_count > _MAX_ITEMS or type_count > _MAX_TYPES:
        raise ValueError("modern playback recording-types V3 exceeds bounded counts")
    item_start = _V3_HEADER_SIZE + type_count * _V3_TYPE_SIZE
    if len(payload) != item_start + item_count * _V3_ITEM_SIZE:
        raise ValueError("modern playback recording-types V3 size is inconsistent")
    items: list[ModernPlaybackRecordingType] = []
    for index in range(item_count):
        offset = item_start + index * _V3_ITEM_SIZE
        start_offset_ms, duration_ms = struct.unpack_from("<QI", payload, offset)
        items.append(
            _parse_type_item(
                base_time_ms,
                start_offset_ms,
                duration_ms,
                payload[offset + 12],
                protocol_version=3,
            )
        )
    return ModernPlaybackRecordingTypePage(page_index, total_pages, marker, tuple(items))


def parse_modern_playback_recording_types_v4_response(
    payload: bytes,
) -> ModernPlaybackRecordingTypePage:
    """Parse one compact V4 type-window fragment."""

    if len(payload) < _V4_HEADER_SIZE or payload[0] != 4:
        raise ValueError("modern playback recording-types V4 response is invalid")
    fragment_count = payload[1]
    fragment_index = payload[2]
    total_pages, page_index = struct.unpack_from("<HH", payload, 3)
    base_time_ms = struct.unpack_from("<Q", payload, 7)[0]
    item_count = struct.unpack_from("<H", payload, 24)[0]
    if fragment_count == 0 or fragment_index >= fragment_count:
        raise ValueError("modern playback recording-types V4 fragment metadata is invalid")
    if item_count > _MAX_ITEMS:
        raise ValueError("modern playback recording-types V4 exceeds bounded item count")
    if len(payload) != _V4_HEADER_SIZE + item_count * _V4_ITEM_SIZE:
        raise ValueError("modern playback recording-types V4 size is inconsistent")
    items: list[ModernPlaybackRecordingType] = []
    for index in range(item_count):
        offset = _V4_HEADER_SIZE + index * _V4_ITEM_SIZE
        start_offset_ms, duration_ms, flags = struct.unpack_from("<III", payload, offset)
        items.append(
            _parse_type_item(
                base_time_ms,
                start_offset_ms,
                duration_ms,
                flags,
                protocol_version=4,
            )
        )
    return ModernPlaybackRecordingTypePage(
        page_index,
        total_pages,
        None,
        tuple(items),
        fragment_index,
        fragment_count,
    )


def _parse_type_item(
    base_time_ms: int,
    start_offset_ms: int,
    duration_ms: int,
    flags: int,
    *,
    protocol_version: int,
) -> ModernPlaybackRecordingType:
    start_ms = base_time_ms + start_offset_ms
    end_ms = start_ms + duration_ms
    if duration_ms == 0 or end_ms > 0x7FFFFFFFFFFFFFFF:
        raise ValueError(
            f"modern playback recording-types V{protocol_version} item is invalid"
        )
    return ModernPlaybackRecordingType(start_ms, end_ms, duration_ms, flags & 1)


def merge_modern_playback_recording_types_v4_fragments(
    fragments: tuple[ModernPlaybackRecordingTypePage, ...],
) -> ModernPlaybackRecordingTypePage:
    """Return type windows only after every V4 fragment is present exactly once."""

    if not fragments:
        raise ValueError("modern playback recording-types V4 fragments are empty")
    expected_count = fragments[0].fragment_count
    if expected_count is None or expected_count > 64 or len(fragments) != expected_count:
        raise ValueError("modern playback recording-types V4 fragment set is incomplete")
    indexed: dict[int, ModernPlaybackRecordingTypePage] = {}
    for fragment in fragments:
        index = fragment.fragment_index
        if (
            fragment.fragment_count != expected_count
            or index is None
            or index in indexed
            or fragment.page_index != fragments[0].page_index
            or fragment.total_pages != fragments[0].total_pages
        ):
            raise ValueError("modern playback recording-types V4 fragments are inconsistent")
        indexed[index] = fragment
    if set(indexed) != set(range(expected_count)):
        raise ValueError("modern playback recording-types V4 fragment set has gaps")
    items = tuple(item for index in range(expected_count) for item in indexed[index].items)
    if len(items) > 1000:
        raise ValueError("modern playback recording-types V4 merged response exceeds bounded items")
    return ModernPlaybackRecordingTypePage(
        fragments[0].page_index,
        fragments[0].total_pages,
        None,
        items,
        expected_count - 1,
        expected_count,
    )
