"""Socket-free codecs for recovered IoTVideo playback-list protocols V3 and V4."""

from __future__ import annotations

import struct

from ...contracts import OnboardRecordingQuery
from .onboard_playback_modern import (
    PLAYBACK_LIST_REQUEST_SIZE,
    ModernPlaybackFile,
    ModernPlaybackPage,
    epoch_milliseconds,
)

PLAYBACK_PROTOCOL_V3 = 3
PLAYBACK_PROTOCOL_V4 = 4
PLAYBACK_V3_RESPONSE_HEADER_SIZE = 26
PLAYBACK_V3_TYPE_SIZE = 17
PLAYBACK_V3_ITEM_SIZE = 13
PLAYBACK_V4_RESPONSE_HEADER_SIZE = 26
PLAYBACK_V4_ITEM_SIZE = 12
_MAX_RESPONSE_ITEMS = 200
_MAX_NATIVE_TYPES = 64


def _validate_request_fields(
    query: OnboardRecordingQuery,
    page_index: int,
    rec_opts: int,
) -> None:
    if not isinstance(query, OnboardRecordingQuery):
        raise ValueError("playback-list query is invalid")
    if type(page_index) is not int or not 0 <= page_index <= 0xFFFFFFFF:
        raise ValueError("playback-list page index is invalid")
    if type(rec_opts) is not int or not 0 <= rec_opts <= 0xFFFFFFFFFFFFFFFF:
        raise ValueError("playback-list recording options are invalid")


def _build_request_prefix(
    query: OnboardRecordingQuery,
    page_index: int,
    protocol_version: int,
) -> bytearray:
    payload = bytearray(PLAYBACK_LIST_REQUEST_SIZE)
    payload[0] = protocol_version
    struct.pack_into("<Q", payload, 1, epoch_milliseconds(query.start_utc))
    struct.pack_into("<Q", payload, 9, epoch_milliseconds(query.end_utc))
    struct.pack_into(">I", payload, 17, page_index)
    struct.pack_into(">I", payload, 21, query.limit)
    return payload


def build_modern_playback_list_v3_request(
    query: OnboardRecordingQuery,
    *,
    page_index: int = 0,
    rec_opts: int = 0,
    ascending: bool = False,
) -> bytes:
    """Build the native 43-byte V3 request carried by BuiltIn command ``16``.

    The native builder retains only the low byte of ``rec_opts`` in V3. The SDK's filter storage
    overlaps that byte, so this bounded implementation emits the default empty filter only.
    """

    _validate_request_fields(query, page_index, rec_opts)
    if type(ascending) is not bool:
        raise ValueError("playback-list ordering flag is invalid")
    payload = _build_request_prefix(query, page_index, PLAYBACK_PROTOCOL_V3)
    payload[25] = rec_opts & 0xFF
    payload[42] = int(ascending)
    return bytes(payload)


def build_modern_playback_list_v4_request(
    query: OnboardRecordingQuery,
    *,
    page_index: int = 0,
    rec_opts: int = 0,
    camera_id: int = -1,
    ascending: bool = False,
) -> bytes:
    """Build the native 43-byte V4 request carried by BuiltIn command ``16``."""

    _validate_request_fields(query, page_index, rec_opts)
    if type(camera_id) is not int or not -128 <= camera_id <= 127:
        raise ValueError("playback-list camera id is invalid")
    if type(ascending) is not bool:
        raise ValueError("playback-list ordering flag is invalid")
    payload = _build_request_prefix(query, page_index, PLAYBACK_PROTOCOL_V4)
    struct.pack_into("<I", payload, 25, rec_opts & 0xFFFFFFFF)
    payload[29] = camera_id & 0xFF
    payload[42] = int(ascending)
    return bytes(payload)


def _decode_native_type(field: bytes) -> str:
    terminator = field.find(0)
    if terminator < 0:
        raise ValueError("modern playback-list type field is invalid")
    try:
        value = field[:terminator].decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError("modern playback-list type is not UTF-8") from exc
    if not value:
        raise ValueError("modern playback-list type is empty")
    return value


def parse_modern_playback_list_v3_response(payload: bytes) -> ModernPlaybackPage:
    """Parse V3's type table and 13-byte items with 64-bit start offsets."""

    if len(payload) < PLAYBACK_V3_RESPONSE_HEADER_SIZE or payload[0] != PLAYBACK_PROTOCOL_V3:
        raise ValueError("modern playback-list V3 response is invalid")
    marker = struct.unpack_from("<i", payload, 1)[0]
    page_index, total_pages = struct.unpack_from("<II", payload, 5)
    item_count = struct.unpack_from("<I", payload, 13)[0]
    base_time_ms = struct.unpack_from("<Q", payload, 17)[0]
    type_count = payload[25]
    if item_count > _MAX_RESPONSE_ITEMS or type_count > _MAX_NATIVE_TYPES:
        raise ValueError("modern playback-list V3 response exceeds bounded counts")
    type_end = PLAYBACK_V3_RESPONSE_HEADER_SIZE + type_count * PLAYBACK_V3_TYPE_SIZE
    expected_size = type_end + item_count * PLAYBACK_V3_ITEM_SIZE
    if len(payload) != expected_size:
        raise ValueError("modern playback-list V3 response size is inconsistent")
    native_types = tuple(
        _decode_native_type(
            payload[
                PLAYBACK_V3_RESPONSE_HEADER_SIZE + index * PLAYBACK_V3_TYPE_SIZE :
                PLAYBACK_V3_RESPONSE_HEADER_SIZE + (index + 1) * PLAYBACK_V3_TYPE_SIZE
            ]
        )
        for index in range(type_count)
    )
    items: list[ModernPlaybackFile] = []
    for index in range(item_count):
        offset = type_end + index * PLAYBACK_V3_ITEM_SIZE
        start_offset_ms, duration_ms = struct.unpack_from("<QI", payload, offset)
        type_index = payload[offset + 12]
        if type_index >= len(native_types):
            raise ValueError("modern playback-list V3 item has an unknown type index")
        start_ms = base_time_ms + start_offset_ms
        end_ms = start_ms + duration_ms
        if duration_ms == 0 or end_ms > 0x7FFFFFFFFFFFFFFF:
            raise ValueError("modern playback-list V3 item has an invalid duration")
        items.append(ModernPlaybackFile(start_ms, end_ms, duration_ms, native_types[type_index]))
    return ModernPlaybackPage(page_index, total_pages, marker, tuple(items))


def parse_modern_playback_list_v4_response(payload: bytes) -> ModernPlaybackPage:
    """Parse V4's compact header and fixed 12-byte numeric-type items."""

    if len(payload) < PLAYBACK_V4_RESPONSE_HEADER_SIZE or payload[0] != PLAYBACK_PROTOCOL_V4:
        raise ValueError("modern playback-list V4 response is invalid")
    fragment_count = payload[1]
    fragment_index = payload[2]
    total_pages, page_index = struct.unpack_from("<HH", payload, 3)
    base_time_ms = struct.unpack_from("<Q", payload, 7)[0]
    item_count = struct.unpack_from("<H", payload, 24)[0]
    if fragment_count == 0 or fragment_index >= fragment_count:
        raise ValueError("modern playback-list V4 fragment metadata is invalid")
    if item_count > _MAX_RESPONSE_ITEMS:
        raise ValueError("modern playback-list V4 response exceeds bounded item count")
    if len(payload) != PLAYBACK_V4_RESPONSE_HEADER_SIZE + item_count * PLAYBACK_V4_ITEM_SIZE:
        raise ValueError("modern playback-list V4 response size is inconsistent")
    items: list[ModernPlaybackFile] = []
    for index in range(item_count):
        offset = PLAYBACK_V4_RESPONSE_HEADER_SIZE + index * PLAYBACK_V4_ITEM_SIZE
        start_offset_ms, duration_ms, flags = struct.unpack_from("<III", payload, offset)
        start_ms = base_time_ms + start_offset_ms
        end_ms = start_ms + duration_ms
        if duration_ms == 0 or end_ms > 0x7FFFFFFFFFFFFFFF:
            raise ValueError("modern playback-list V4 item has an invalid duration")
        # The native PlaybackFile parser deliberately exposes only bit zero as a decimal string.
        items.append(ModernPlaybackFile(start_ms, end_ms, duration_ms, str(flags & 1)))
    return ModernPlaybackPage(
        page_index,
        total_pages,
        None,
        tuple(items),
        fragment_index,
        fragment_count,
    )


def merge_modern_playback_v4_fragments(
    fragments: tuple[ModernPlaybackPage, ...],
) -> ModernPlaybackPage:
    """Merge one complete, ordered-independent V4 response without hiding missing fragments."""

    if not fragments:
        raise ValueError("modern playback-list V4 fragments are empty")
    expected_count = fragments[0].fragment_count
    if expected_count is None or len(fragments) != expected_count or expected_count > 64:
        raise ValueError("modern playback-list V4 fragment set is incomplete")
    indexed: dict[int, ModernPlaybackPage] = {}
    for fragment in fragments:
        index = fragment.fragment_index
        if (
            fragment.fragment_count != expected_count
            or index is None
            or index in indexed
            or fragment.page_index != fragments[0].page_index
            or fragment.total_pages != fragments[0].total_pages
        ):
            raise ValueError("modern playback-list V4 fragments are inconsistent")
        indexed[index] = fragment
    if set(indexed) != set(range(expected_count)):
        raise ValueError("modern playback-list V4 fragment set has gaps")
    items = tuple(item for index in range(expected_count) for item in indexed[index].items)
    if len(items) > 1000:
        raise ValueError("modern playback-list V4 merged response exceeds bounded items")
    return ModernPlaybackPage(
        fragments[0].page_index,
        fragments[0].total_pages,
        None,
        items,
        expected_count - 1,
        expected_count,
    )
