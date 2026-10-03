"""Offline fragment envelopes; no decryption, sequencing or media inference."""

import struct
from dataclasses import dataclass, field


@dataclass(frozen=True)
class RTCFragment:
    kind: int
    fragment_id: int
    metadata: bytes = field(repr=False)
    payload: bytes = field(repr=False)

    @property
    def error_code(self) -> int | None:
        return struct.unpack_from("<I", self.metadata)[0] if self.kind == 0xF3 else None


def parse_rtc_fragment(frame: bytes) -> RTCFragment:
    """Parse one complete plaintext fragment, retaining unknown bytes 12..19.

    Caller must establish encryption/session provenance. In particular this
    parser does not imply that selective decryption supports 0xf0 or 0xf3.
    The record bound is local policy aligned with the outer TCP framer.
    """
    if not isinstance(frame, bytes) or not 20 <= len(frame) <= 0x8400 - 20:
        raise ValueError("RTC fragment size is invalid")
    kind = struct.unpack_from("<H", frame)[0]
    if kind not in (0xF0, 0xF1, 0xF2, 0xF3):
        raise ValueError("RTC fragment type is unsupported")
    if struct.unpack_from("<I", frame, 4)[0] != len(frame) - 8:
        raise ValueError("RTC fragment length does not match")
    # SDK keys its map by signed int, logging frag_id with %d.
    fragment_id = struct.unpack_from("<i", frame, 8)[0]
    return RTCFragment(kind, fragment_id, frame[12:20], frame[20:])
