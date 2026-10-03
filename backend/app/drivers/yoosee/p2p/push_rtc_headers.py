"""Offline structural parsing of decrypted RTC header-only records."""

import struct

from .push_rtc import selective_rtc_cipher_span


def parse_rtc_header_entries(frame: bytes) -> tuple[bytes, ...]:
    """Return opaque twenty-byte entries from one complete type-0x81 record.

    Caller owns decryption and session provenance. This does not authenticate
    contents or identify codecs/capabilities. Unknown prefix bytes are preserved
    in the caller's record, not assigned speculative meanings here.
    """
    selective_rtc_cipher_span(frame)
    if struct.unpack_from("<H", frame)[0] != 0x81:
        raise ValueError("RTC header-only type is unsupported")
    if len(frame) < 10:
        raise ValueError("RTC header-only prefix is truncated")
    count = frame[9]
    if len(frame) != 10 + count * 20:
        raise ValueError("RTC header entry count does not match record length")
    return tuple(frame[start:start + 20] for start in range(10, len(frame), 20))
