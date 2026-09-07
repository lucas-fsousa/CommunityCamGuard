"""Socket-free codecs for IoTVideo playback-list protocols V1 and V2."""

from __future__ import annotations

import struct
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from ...contracts import OnboardRecordingQuery

PLAYBACK_GET_LIST_V1_COMMAND = 0
PLAYBACK_GET_LIST_V2_COMMAND = 16
PLAYBACK_PROTOCOL_V1 = 1
PLAYBACK_PROTOCOL_V2 = 2
PLAYBACK_LIST_REQUEST_SIZE = 43
PLAYBACK_FILTER_SIZE = 18
PLAYBACK_V1_RESPONSE_HEADER_SIZE = 13
PLAYBACK_V1_ITEM_SIZE = 33
PLAYBACK_V2_RESPONSE_HEADER_SIZE = 26
PLAYBACK_V2_TYPE_SIZE = 17
PLAYBACK_V2_ITEM_SIZE = 9


@dataclass(frozen=True, slots=True)
class ModernPlaybackFile:
    start_ms: int
    end_ms: int
    duration_ms: int
    native_type: str


@dataclass(frozen=True, slots=True)
class ModernPlaybackPage:
    page_index: int
    total_pages: int
    marker: int | None
    items: tuple[ModernPlaybackFile, ...]
    fragment_index: int | None = None
    fragment_count: int | None = None


def epoch_milliseconds(value: datetime) -> int:
    if value.tzinfo is None or value.utcoffset() is None or value.utcoffset() != timedelta(0):
        raise ValueError("playback-list timestamps must be UTC")
    elapsed = value - datetime(1970, 1, 1, tzinfo=UTC)
    milliseconds = (
        elapsed.days * 86_400_000 + elapsed.seconds * 1000 + elapsed.microseconds // 1000
    )
    if milliseconds < 0:
        raise ValueError("playback-list timestamps must not precede the Unix epoch")
    return milliseconds


def build_modern_playback_list_v2_request(
    query: OnboardRecordingQuery,
    *,
    page_index: int = 0,
    filter_type: str = "",
) -> bytes:
    """Build the 43-byte payload passed to IoTVideo BuiltIn command ``16``.

    ``PlaybackFileMgr._make_proto_req_data`` converts the SDK's microsecond inputs to epoch
    milliseconds, encodes page values in network byte order and reserves 18 bytes for a
    NUL-terminated filter. V3/V4 intentionally remain unsupported until their option bytes are
    fully decoded and confirmed.
    """

    if not isinstance(query, OnboardRecordingQuery):
        raise ValueError("playback-list query is invalid")
    if type(page_index) is not int or not 0 <= page_index <= 0xFFFFFFFF:
        raise ValueError("playback-list page index is invalid")
    try:
        encoded_filter = filter_type.encode("ascii")
    except (AttributeError, UnicodeEncodeError) as exc:
        raise ValueError("playback-list filter must be ASCII") from exc
    if len(encoded_filter) >= PLAYBACK_FILTER_SIZE or b"\x00" in encoded_filter:
        raise ValueError("playback-list filter is too long or contains NUL")

    payload = bytearray(PLAYBACK_LIST_REQUEST_SIZE)
    payload[0] = PLAYBACK_PROTOCOL_V2
    struct.pack_into("<Q", payload, 1, epoch_milliseconds(query.start_utc))
    struct.pack_into("<Q", payload, 9, epoch_milliseconds(query.end_utc))
    struct.pack_into(">I", payload, 17, page_index)
    struct.pack_into(">I", payload, 21, query.limit)
    payload[25 : 25 + len(encoded_filter)] = encoded_filter
    return bytes(payload)


def build_modern_playback_list_v1_request(
    query: OnboardRecordingQuery,
    *,
    page_index: int = 0,
) -> bytes:
    """Build the recovered 43-byte V1 body passed to BuiltIn command ``0``."""

    if not isinstance(query, OnboardRecordingQuery):
        raise ValueError("playback-list query is invalid")
    if type(page_index) is not int or not 0 <= page_index <= 0xFFFFFFFF:
        raise ValueError("playback-list page index is invalid")
    payload = bytearray(PLAYBACK_LIST_REQUEST_SIZE)
    payload[0] = PLAYBACK_PROTOCOL_V1
    struct.pack_into("<Q", payload, 1, epoch_milliseconds(query.start_utc))
    struct.pack_into("<Q", payload, 9, epoch_milliseconds(query.end_utc))
    struct.pack_into(">I", payload, 17, page_index)
    struct.pack_into(">I", payload, 21, query.limit)
    return bytes(payload)


def unpack_modern_playback_list_v2_request(payload: bytes) -> dict[str, object]:
    """Decode test/diagnostic V2 payloads without accepting V3/V4 layouts."""

    if len(payload) != PLAYBACK_LIST_REQUEST_SIZE or payload[0] != PLAYBACK_PROTOCOL_V2:
        raise ValueError("modern playback-list V2 request is invalid")
    filter_field = payload[25:43]
    terminator = filter_field.find(0)
    if terminator < 0:
        raise ValueError("modern playback-list filter is not terminated")
    try:
        filter_type = filter_field[:terminator].decode("ascii")
    except UnicodeDecodeError as exc:
        raise ValueError("modern playback-list filter is invalid") from exc
    if any(filter_field[terminator + 1 :]):
        raise ValueError("modern playback-list reserved bytes are not zero")
    return {
        "start_ms": struct.unpack_from("<Q", payload, 1)[0],
        "end_ms": struct.unpack_from("<Q", payload, 9)[0],
        "page_index": struct.unpack_from(">I", payload, 17)[0],
        "count_per_page": struct.unpack_from(">I", payload, 21)[0],
        "filter_type": filter_type,
    }


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


def parse_modern_playback_list_v2_response(payload: bytes) -> ModernPlaybackPage:
    """Parse the V2 response recovered from ``_rsp_list_parse_v2<PlaybackFile>``.

    The native marker at offset 1 is retained without assigning semantics beyond the observed
    ``-1`` end condition. Native recording-type strings also remain unnormalized until a real
    camera response establishes their model/firmware vocabulary.
    """

    if len(payload) < PLAYBACK_V2_RESPONSE_HEADER_SIZE or payload[0] != PLAYBACK_PROTOCOL_V2:
        raise ValueError("modern playback-list V2 response is invalid")
    marker = struct.unpack_from("<i", payload, 1)[0]
    page_index, total_pages = struct.unpack_from("<II", payload, 5)
    item_count = struct.unpack_from("<I", payload, 13)[0]
    base_time_ms = struct.unpack_from("<Q", payload, 17)[0]
    type_count = payload[25]
    if item_count > 200 or type_count > 64:
        raise ValueError("modern playback-list V2 response exceeds bounded counts")
    type_end = PLAYBACK_V2_RESPONSE_HEADER_SIZE + type_count * PLAYBACK_V2_TYPE_SIZE
    expected_size = type_end + item_count * PLAYBACK_V2_ITEM_SIZE
    if len(payload) != expected_size:
        raise ValueError("modern playback-list V2 response size is inconsistent")
    native_types = tuple(
        _decode_native_type(
            payload[
                PLAYBACK_V2_RESPONSE_HEADER_SIZE + index * PLAYBACK_V2_TYPE_SIZE :
                PLAYBACK_V2_RESPONSE_HEADER_SIZE + (index + 1) * PLAYBACK_V2_TYPE_SIZE
            ]
        )
        for index in range(type_count)
    )
    items: list[ModernPlaybackFile] = []
    for index in range(item_count):
        offset = type_end + index * PLAYBACK_V2_ITEM_SIZE
        start_offset_ms, duration_ms = struct.unpack_from("<II", payload, offset)
        type_index = payload[offset + 8]
        if type_index >= len(native_types):
            raise ValueError("modern playback-list V2 item has an unknown type index")
        start_ms = base_time_ms + start_offset_ms
        end_ms = start_ms + duration_ms
        if duration_ms == 0 or end_ms > 0x7FFFFFFFFFFFFFFF:
            raise ValueError("modern playback-list V2 item has an invalid duration")
        items.append(ModernPlaybackFile(start_ms, end_ms, duration_ms, native_types[type_index]))
    return ModernPlaybackPage(page_index, total_pages, marker, tuple(items))


def parse_modern_playback_list_v1_response(payload: bytes) -> ModernPlaybackPage:
    """Parse the fixed 13-byte-header/33-byte-item V1 playback-list response."""

    if len(payload) < PLAYBACK_V1_RESPONSE_HEADER_SIZE or payload[0] != PLAYBACK_PROTOCOL_V1:
        raise ValueError("modern playback-list V1 response is invalid")
    page_index, total_pages, item_count = struct.unpack_from("<III", payload, 1)
    if item_count > 200:
        raise ValueError("modern playback-list V1 response exceeds bounded item count")
    if len(payload) != PLAYBACK_V1_RESPONSE_HEADER_SIZE + item_count * PLAYBACK_V1_ITEM_SIZE:
        raise ValueError("modern playback-list V1 response size is inconsistent")
    items: list[ModernPlaybackFile] = []
    for index in range(item_count):
        offset = PLAYBACK_V1_RESPONSE_HEADER_SIZE + index * PLAYBACK_V1_ITEM_SIZE
        start_ms, end_ms = struct.unpack_from("<QQ", payload, offset)
        native_type = _decode_native_type(payload[offset + 16 : offset + 33])
        if end_ms <= start_ms or end_ms > 0x7FFFFFFFFFFFFFFF:
            raise ValueError("modern playback-list V1 item has an invalid time range")
        items.append(ModernPlaybackFile(start_ms, end_ms, end_ms - start_ms, native_type))
    return ModernPlaybackPage(page_index, total_pages, None, tuple(items))
