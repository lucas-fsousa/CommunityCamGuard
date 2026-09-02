"""Socket-free codec for the recovered IoTVideo V2 playback-list request."""

from __future__ import annotations

import struct
from datetime import UTC, datetime, timedelta

from ...contracts import OnboardRecordingQuery

PLAYBACK_GET_LIST_V2_COMMAND = 16
PLAYBACK_PROTOCOL_V2 = 2
PLAYBACK_LIST_REQUEST_SIZE = 43
PLAYBACK_FILTER_SIZE = 18


def _epoch_milliseconds(value: datetime) -> int:
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
    struct.pack_into("<Q", payload, 1, _epoch_milliseconds(query.start_utc))
    struct.pack_into("<Q", payload, 9, _epoch_milliseconds(query.end_utc))
    struct.pack_into(">I", payload, 17, page_index)
    struct.pack_into(">I", payload, 21, query.limit)
    payload[25 : 25 + len(encoded_filter)] = encoded_filter
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
