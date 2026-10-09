"""Passive MTP resource-table parsing; never selects or contacts a relay."""

from __future__ import annotations

import ipaddress
import struct
from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class MTPRelayDescriptor:
    family: int
    index: int
    flags: int
    address: str = field(repr=False)
    port: int = field(repr=False)


def parse_mtp_relays(
    frame: bytes, *, expected_session_id: int, expected_link_id: int,
) -> tuple[MTPRelayDescriptor, ...] | None:
    """Decode an authenticated, uncompressed A3's independent address tables.

    The caller must decrypt/authenticate the broker response first. Correlation
    alone is not authentication. Missing tables are unknown, not empty. Reject
    oversized counts rather than reproducing the SDK's in-place clamping.
    Flags are deliberately opaque: MTP's paired-family bit-2 selection differs
    from E4 flags. No family pairing, endpoint policy or platform is inferred.
    """
    if not 0x7A <= len(frame) <= 0x7A + 32 * 16 + 16 * 28:
        return None
    if frame[:2] not in (b"\x7e\xa3", b"\x7f\xa3"):
        return None
    if struct.unpack_from("<H", frame, 2)[0] != len(frame):
        return None
    if struct.unpack_from("<Q", frame, 4)[0] != expected_session_id:
        return None
    if struct.unpack_from("<I", frame, 0x1C)[0] != expected_link_id:
        return None
    flags = struct.unpack_from("<I", frame, 0x14)[0]
    if ((flags >> 16) & 3) != 2 or flags & ((1 << 20) | 1):
        return None
    v4_count, v6_count = frame[0x78:0x7A]
    if v4_count > 32 or v6_count > 16:
        return None
    if 0x7A + v4_count * 16 + v6_count * 28 != len(frame):
        return None
    result = []
    offset = 0x7A
    for family, count, stride, width in ((4, v4_count, 16, 4), (6, v6_count, 28, 16)):
        for index in range(count):
            entry_flags = struct.unpack_from("<H", frame, offset + 8)[0]
            # SDK copies this directly into sockaddr.sin_port, then ntohs for logs.
            port = struct.unpack_from(">H", frame, offset + 10)[0]
            address = str(ipaddress.ip_address(frame[offset + 12:offset + 12 + width]))
            result.append(MTPRelayDescriptor(family, index, entry_flags, address, port))
            offset += stride
    return tuple(result)
