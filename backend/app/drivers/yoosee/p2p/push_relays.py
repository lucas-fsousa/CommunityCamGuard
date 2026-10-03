"""Socket-free relay descriptors from an already authenticated E4 distribution."""

from __future__ import annotations

import ipaddress
import struct
from dataclasses import dataclass, field

from .platform_metadata import parse_push_stream_platform_metadata


@dataclass(frozen=True, slots=True)
class PushRelayDescriptor:
    """Advertisements only: not a selected route or permission to connect.

    IPv4/IPv6 entries are independent; their pairing and terminal-mode selection
    are not inferred. Do not log/serialize addresses without explicit redaction.
    """

    family: int
    address: str = field(repr=False)
    port: int = field(repr=False)
    flags: int

    @property
    def advertises_tcp(self) -> bool | None:
        return bool(self.flags & 1) if self.family == 4 else None

    @property
    def advertises_udp(self) -> bool | None:
        return bool(self.flags & 2) if self.family == 4 else None

    @property
    def cluster_id(self) -> int | None:
        return (self.flags >> 4) & 0xF if self.family == 4 else None


def parse_push_relays(
    frame: bytes, *, expected_device_id: int, expected_link_id: int,
) -> tuple[PushRelayDescriptor, ...] | None:
    """Read bounded advertised endpoints without DNS, sockets or address probing.

    IDs do not authenticate E4. The caller must supply trusted decrypted input.
    Local admission permits at most eight entries per family and no trailing
    extension. Zero-port descriptors are retained as inactive advertisements,
    not silently promoted to a default port. All addresses, including special
    ranges, are data here: a future connector needs a separate endpoint policy.
    """
    if parse_push_stream_platform_metadata(
        frame, expected_device_id=expected_device_id, expected_link_id=expected_link_id,
    ) is None:
        return None
    offset = 0x88 + struct.unpack_from("<H", frame, 0x1E)[0]
    v4_count, v6_count = frame[offset:offset + 2]
    offset += 2
    if max(v4_count, v6_count) > 8 or offset + v4_count * 16 + v6_count * 28 != len(frame):
        return None
    result = []
    for family, count, size, address_size in ((4, v4_count, 16, 4), (6, v6_count, 28, 16)):
        for _ in range(count):
            flags, port = struct.unpack_from("<HH", frame, offset + 8)
            address = str(ipaddress.ip_address(frame[offset + 12:offset + 12 + address_size]))
            result.append(PushRelayDescriptor(family, address, port, flags))
            offset += size
    return tuple(result)
