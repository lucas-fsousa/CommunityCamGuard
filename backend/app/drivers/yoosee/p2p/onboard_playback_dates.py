"""Bounded codecs for IoTVideo BuiltIn command 18 (dates with onboard footage)."""

from __future__ import annotations

import struct
from dataclasses import dataclass

PLAYBACK_GET_DATE_LIST_COMMAND = 18
_V23_HEADER_SIZE = 26
_V23_TYPE_SIZE = 17
_V2_ITEM_SIZE = 9
_V3_ITEM_SIZE = 13
_V4_HEADER_SIZE = 26
_V4_ITEM_SIZE = 12
_MAX_ITEMS = 200
_MAX_TYPES = 64


@dataclass(frozen=True, slots=True)
class ModernPlaybackDatePage:
    page_index: int
    total_pages: int
    marker: int | None
    dates_ms: tuple[int, ...]
    fragment_index: int | None = None
    fragment_count: int | None = None


def _parse_v23(payload: bytes, protocol_version: int) -> ModernPlaybackDatePage:
    item_size = _V2_ITEM_SIZE if protocol_version == 2 else _V3_ITEM_SIZE
    if len(payload) < _V23_HEADER_SIZE or payload[0] != protocol_version:
        raise ValueError(f"modern playback-date V{protocol_version} response is invalid")
    marker = struct.unpack_from("<i", payload, 1)[0]
    page_index, total_pages = struct.unpack_from("<II", payload, 5)
    item_count = struct.unpack_from("<I", payload, 13)[0]
    base_time_ms = struct.unpack_from("<Q", payload, 17)[0]
    type_count = payload[25]
    if item_count > _MAX_ITEMS or type_count > _MAX_TYPES:
        raise ValueError(f"modern playback-date V{protocol_version} exceeds bounded counts")
    item_start = _V23_HEADER_SIZE + type_count * _V23_TYPE_SIZE
    if len(payload) != item_start + item_count * item_size:
        raise ValueError(f"modern playback-date V{protocol_version} size is inconsistent")
    dates: list[int] = []
    for index in range(item_count):
        offset = item_start + index * item_size
        relative_ms = struct.unpack_from("<I" if protocol_version == 2 else "<Q", payload, offset)[0]
        date_ms = base_time_ms + relative_ms
        if date_ms > 0x7FFFFFFFFFFFFFFF:
            raise ValueError(f"modern playback-date V{protocol_version} timestamp is invalid")
        dates.append(date_ms)
    return ModernPlaybackDatePage(page_index, total_pages, marker, tuple(dates))


def parse_modern_playback_date_v2_response(payload: bytes) -> ModernPlaybackDatePage:
    """Parse V2 dates, whose relevant item field is a 32-bit offset in milliseconds."""

    return _parse_v23(payload, 2)


def parse_modern_playback_date_v3_response(payload: bytes) -> ModernPlaybackDatePage:
    """Parse V3 dates, whose relevant item field is a 64-bit offset in milliseconds."""

    return _parse_v23(payload, 3)


def parse_modern_playback_date_v4_response(payload: bytes) -> ModernPlaybackDatePage:
    """Parse one V4 date-list fragment."""

    if len(payload) < _V4_HEADER_SIZE or payload[0] != 4:
        raise ValueError("modern playback-date V4 response is invalid")
    fragment_count = payload[1]
    fragment_index = payload[2]
    total_pages, page_index = struct.unpack_from("<HH", payload, 3)
    base_time_ms = struct.unpack_from("<Q", payload, 7)[0]
    item_count = struct.unpack_from("<H", payload, 24)[0]
    if fragment_count == 0 or fragment_index >= fragment_count:
        raise ValueError("modern playback-date V4 fragment metadata is invalid")
    if item_count > _MAX_ITEMS:
        raise ValueError("modern playback-date V4 exceeds bounded item count")
    if len(payload) != _V4_HEADER_SIZE + item_count * _V4_ITEM_SIZE:
        raise ValueError("modern playback-date V4 size is inconsistent")
    dates: list[int] = []
    for index in range(item_count):
        offset = _V4_HEADER_SIZE + index * _V4_ITEM_SIZE
        relative_ms = struct.unpack_from("<I", payload, offset)[0]
        date_ms = base_time_ms + relative_ms
        if date_ms > 0x7FFFFFFFFFFFFFFF:
            raise ValueError("modern playback-date V4 timestamp is invalid")
        dates.append(date_ms)
    return ModernPlaybackDatePage(
        page_index,
        total_pages,
        None,
        tuple(dates),
        fragment_index,
        fragment_count,
    )


def merge_modern_playback_date_v4_fragments(
    fragments: tuple[ModernPlaybackDatePage, ...],
) -> ModernPlaybackDatePage:
    """Return dates only after every V4 response fragment is present exactly once."""

    if not fragments:
        raise ValueError("modern playback-date V4 fragments are empty")
    expected_count = fragments[0].fragment_count
    if expected_count is None or expected_count > 64 or len(fragments) != expected_count:
        raise ValueError("modern playback-date V4 fragment set is incomplete")
    indexed: dict[int, ModernPlaybackDatePage] = {}
    for fragment in fragments:
        index = fragment.fragment_index
        if (
            fragment.fragment_count != expected_count
            or index is None
            or index in indexed
            or fragment.page_index != fragments[0].page_index
            or fragment.total_pages != fragments[0].total_pages
        ):
            raise ValueError("modern playback-date V4 fragments are inconsistent")
        indexed[index] = fragment
    if set(indexed) != set(range(expected_count)):
        raise ValueError("modern playback-date V4 fragment set has gaps")
    dates_ms = tuple(date for index in range(expected_count) for date in indexed[index].dates_ms)
    if len(dates_ms) > 1000:
        raise ValueError("modern playback-date V4 merged response exceeds bounded items")
    return ModernPlaybackDatePage(
        fragments[0].page_index,
        fragments[0].total_pages,
        None,
        dates_ms,
        expected_count - 1,
        expected_count,
    )
