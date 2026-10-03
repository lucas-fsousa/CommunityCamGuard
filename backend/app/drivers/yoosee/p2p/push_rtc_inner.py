"""Offline, nonrecursive boundaries for a completely assembled RTC stream."""

import struct

from .push_rtc_headers import parse_rtc_header_entries


def split_inner_rtc_records(data: bytes) -> tuple[bytes, ...]:
    """Validate all boundaries before returning any opaque records.

    Local limits: 256 KiB total and 256 records. Inner records can exceed the
    outer relay frame bound because they have already been reassembled.
    Nested fragments, unknown types, empty bodies and partial tails fail closed.
    This does not decrypt or certify AV payloads, codecs or session provenance.
    """
    if not isinstance(data, bytes) or not 1 <= len(data) <= 256 * 1024:
        raise ValueError("RTC inner stream size is invalid")
    spans: list[tuple[int, int]] = []
    offset = 0
    while offset < len(data):
        if len(spans) >= 256 or len(data) - offset < 8:
            raise ValueError("RTC inner record count or prefix is invalid")
        kind, _, body_size = struct.unpack_from("<HHI", data, offset)
        minimum = {0x80: 24, 0x81: 10, 0x82: 9, 0x83: 10}.get(kind)
        if minimum is None:
            raise ValueError("RTC inner type is unsupported or nested")
        size = body_size + 8
        end = offset + size
        if size < minimum or end > len(data):
            raise ValueError("RTC inner record length is invalid")
        if kind in (0x81, 0x83):
            parse_rtc_header_entries(data[offset:end])
        spans.append((offset, end))
        offset = end
    return tuple(data[start:end] for start, end in spans)
