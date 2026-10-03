"""Offline bounds for the SDK's selective RTC encryption mode (not a decoder)."""

import struct

_OFFSETS = {0x80: 24, 0x81: 8, 0x82: 8, 0x83: 8, 0xF1: 20, 0xF2: 20}
_MAX_RTC_BYTES = 0x8400 - 20  # Local bound aligned with the TCP outer framer.


def selective_rtc_cipher_span(frame: bytes) -> tuple[int, int]:
    """Return (offset, byte count) for explicit SDK encryption mode 2 only.

    Does not decrypt, authorize a peer, infer the session's encryption mode or
    validate media contents. Mode 2 is an encryption setting, not platform 2.
    Cipher block/remainder handling and key provenance require separate evidence.
    Unknown record types fail closed rather than inheriting the SDK's no-op.
    """
    if not isinstance(frame, bytes) or not 8 <= len(frame) <= _MAX_RTC_BYTES:
        raise ValueError("RTC record size is invalid")
    kind = struct.unpack_from("<H", frame)[0]
    if struct.unpack_from("<I", frame, 4)[0] != len(frame) - 8:
        raise ValueError("RTC record length does not match")
    offset = _OFFSETS.get(kind)
    if offset is None:
        raise ValueError("RTC selective encryption type is unsupported")
    if len(frame) < offset:
        raise ValueError("RTC record is shorter than its clear prefix")
    return offset, len(frame) - offset
