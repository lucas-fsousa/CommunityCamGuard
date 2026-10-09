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


@dataclass(frozen=True, slots=True)
class MTPRelayAdvertisement:
    """Known table plus uninterpreted extension, not a connection decision."""

    entries: tuple[MTPRelayDescriptor, ...]
    options: int
    extension: bytes = field(repr=False)


def parse_mtp_relays(
    frame: bytes, *, expected_session_id: int, expected_link_id: int,
) -> tuple[MTPRelayDescriptor, ...] | None:
    """Strict compatibility API: reject any unrecognized extension."""
    advertisement = inspect_mtp_relay_advertisement(
        frame, expected_session_id=expected_session_id, expected_link_id=expected_link_id,
    )
    if advertisement is None or advertisement.extension:
        return None
    return advertisement.entries


def inspect_mtp_relay_advertisement(
    frame: bytes, *, expected_session_id: int, expected_link_id: int,
) -> MTPRelayAdvertisement | None:
    """Inspect the known A3 table without treating its opaque suffix as understood.

    The caller must decrypt/authenticate the broker response first. Correlation
    alone is not authentication. Missing tables are unknown, not empty. Reject
    oversized counts rather than reproducing the SDK's in-place clamping.
    Up to 64 suffix bytes are retained explicitly (a local diagnostic budget,
    not an SDK limit). They cannot supply addresses, platform or permissions.
    Flags are deliberately opaque: MTP's paired-family bit-2 selection differs
    from E4 flags. No family pairing, endpoint policy or platform is inferred.
    """
    if not 0x7A <= len(frame) <= 0x7A + 32 * 16 + 16 * 28 + 64:
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
    table_end = 0x7A + v4_count * 16 + v6_count * 28
    if not 0 <= len(frame) - table_end <= 64:
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
    return MTPRelayAdvertisement(
        tuple(result), struct.unpack_from("<H", frame, 0x18)[0], bytes(frame[table_end:]),
    )
