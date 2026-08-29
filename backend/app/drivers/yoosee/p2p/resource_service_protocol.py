"""Socket-free codec for the read-only Yoosee alarm-resource service carrier."""

from __future__ import annotations

import json
import secrets
import struct
from dataclasses import dataclass

from .contracts import CertifiedNode
from .wire import finish_mode2, new_header, randomized_flags

_SERVICE_NAME = "HTTP_PROXY/REQ"
_CATALOG_PATH = "resfile/queryres"


@dataclass(frozen=True, slots=True)
class FragmentPacket:
    decoded_header: bytes
    identity: int
    sequence: int
    original_length: int
    total: int
    index: int
    large_payload: bool
    payload: bytes


class FragmentReassembler:
    """Bounded reassembly for native ``0x70/0x01`` packets."""

    def __init__(self, max_groups: int = 8):
        if not 1 <= max_groups <= 16:
            raise ValueError("fragment group limit must be between 1 and 16")
        self._max_groups = max_groups
        self._groups: dict[tuple[int, int, int, int, bool], dict[int, bytes]] = {}

    def add(self, packet: FragmentPacket) -> bytes | None:
        if not 1 <= packet.total <= 0x7F or not 0 <= packet.index < packet.total:
            raise ValueError("fragment index/count is invalid")
        key = (
            packet.identity,
            packet.sequence,
            packet.original_length,
            packet.total,
            packet.large_payload,
        )
        if key not in self._groups:
            if len(self._groups) >= self._max_groups:
                raise ValueError("too many incomplete fragment groups")
            self._groups[key] = {}
        parts = self._groups[key]
        previous = parts.get(packet.index)
        if previous is not None and previous != packet.payload:
            self._groups.pop(key, None)
            raise ValueError("conflicting duplicate fragment")
        parts[packet.index] = packet.payload
        if len(parts) != packet.total:
            return None
        ordered = [parts[index] for index in range(packet.total)]
        self._groups.pop(key, None)
        if packet.total > 1:
            chunk_size = len(ordered[0])
            if not chunk_size or any(len(part) != chunk_size for part in ordered[:-1]):
                raise ValueError("fragment chunks have inconsistent sizes")
            if len(ordered[-1]) > chunk_size:
                raise ValueError("final fragment is larger than preceding chunks")
        assembled = b"".join(ordered)
        if len(assembled) != packet.original_length:
            raise ValueError("reassembled frame length does not match the fragment header")
        return assembled


def _validate_catalog_query(query: dict[str, object]) -> None:
    expected = {"pageSize", "curPage", "resTypes", "bySys", "accessId"}
    by_system = query.get("bySys")
    if by_system == 1:
        expected.add("keyWord")
    if set(query) != expected:
        raise ValueError("alarm-resource query has unexpected fields")
    if (
        query.get("pageSize") != 20
        or query.get("curPage") != 0
        or query.get("resTypes") != [4]
        or by_system not in (0, 1)
    ):
        raise ValueError("alarm-resource query is outside the read-only contract")
    access_id = query.get("accessId")
    if not isinstance(access_id, str) or not access_id.lstrip("-").isdigit():
        raise ValueError("alarm-resource query has an invalid access id")
    if by_system == 1:
        keyword = query.get("keyWord")
        if not isinstance(keyword, str) or not keyword.startswith("language_"):
            raise ValueError("system alarm-resource query has an invalid language key")


def build_alarm_voice_catalog_request(
    node: CertifiedNode,
    query: dict[str, object],
    sequence: int,
) -> bytes:
    """Build only the fixed POST ``resfile/queryres`` service request."""

    _validate_catalog_query(query)
    envelope = {
        "http": {"url": f"/{_CATALOG_PATH}", "type": "POST"},
        "data": query,
    }
    payload = json.dumps(envelope, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    service = _SERVICE_NAME.encode("ascii")
    length = 0x20 + len(service) + 1 + len(payload) + 1
    frame = new_header(
        0xC0,
        length,
        node.session_id,
        sequence,
        randomized_flags(mode=2, proc=3),
    )
    frame[0] = 0x7E
    frame[0x1C] = 1
    frame[0x1D] = len(service)
    struct.pack_into("<H", frame, 0x1E, len(payload))
    cursor = 0x20
    frame[cursor : cursor + len(service)] = service
    cursor += len(service) + 1
    frame[cursor : cursor + len(payload)] = payload
    return finish_mode2(frame, node.session_key)


def parse_alarm_voice_catalog_response(frame: bytes) -> tuple[int, int, bytes] | None:
    """Parse one uncompressed C1 response into correlation, status and JSON payload."""

    if len(frame) < 0x20 or frame[1] != 0xC1:
        return None
    correlation_id = struct.unpack_from("<I", frame, 0x10)[0]
    status_code = struct.unpack_from("<H", frame, 0x1C)[0]
    if not (frame[0x18] & 1):
        return correlation_id, status_code, b""
    payload_length = struct.unpack_from("<H", frame, 0x1E)[0]
    if payload_length > 0x7800 or 0x20 + payload_length > len(frame):
        return None
    return correlation_id, status_code, frame[0x20 : 0x20 + payload_length]


def encode_fragment_header(decoded: bytes, *, mask: int | None = None) -> bytes:
    """Apply the native XOR/OR checksum protection to a 24-byte fragment header."""

    if len(decoded) < 0x18 or decoded[0] != 0x70:
        raise ValueError("invalid decoded fragment header")
    frame = bytearray(decoded)
    random_mask = secrets.randbits(16) if mask is None else int(mask)
    if not 0 <= random_mask <= 0xFFFF:
        raise ValueError("fragment mask must fit in 16 bits")
    struct.pack_into("<H", frame, 0x14, random_mask)
    checksum = random_mask
    for offset in range(4, 0x14, 2):
        value = struct.unpack_from("<H", frame, offset)[0]
        checksum |= value
        struct.pack_into("<H", frame, offset, value ^ random_mask)
    struct.pack_into("<H", frame, 0x16, checksum)
    return bytes(frame)


def decode_fragment_packet(wire: bytes) -> FragmentPacket:
    """Validate and decode one fragment without touching its opaque payload."""

    if len(wire) < 0x18 or wire[:2] != b"\x70\x01":
        raise ValueError("not a gute fragment-data packet")
    if struct.unpack_from("<H", wire, 2)[0] != len(wire):
        raise ValueError("fragment packet length is invalid")
    frame = bytearray(wire)
    mask = struct.unpack_from("<H", frame, 0x14)[0]
    checksum = mask
    for offset in range(4, 0x14, 2):
        value = struct.unpack_from("<H", frame, offset)[0] ^ mask
        struct.pack_into("<H", frame, offset, value)
        checksum |= value
    if checksum != struct.unpack_from("<H", frame, 0x16)[0]:
        raise ValueError("fragment header checksum is invalid")
    count_and_flags = frame[0x12]
    return FragmentPacket(
        bytes(frame[:0x18]),
        struct.unpack_from("<Q", frame, 4)[0],
        struct.unpack_from("<I", frame, 0x0C)[0],
        struct.unpack_from("<H", frame, 0x10)[0],
        count_and_flags & 0x7F,
        frame[0x13],
        bool(count_and_flags & 0x80),
        bytes(frame[0x18:]),
    )


def build_fragment_ack(packet: FragmentPacket, *, mask: int | None = None) -> bytes:
    """Build the exact ``0x70/0x02`` ACK for one validated fragment."""

    ack = bytearray(0x18)
    ack[:4] = b"\x70\x02\x18\x00"
    ack[4:0x14] = packet.decoded_header[4:0x14]
    return encode_fragment_header(bytes(ack), mask=mask)


def compressed_gute_payload_length(frame: bytes) -> int | None:
    """Return the advertised output size, or ``None`` when the frame is not compressed."""

    if len(frame) < 0x18:
        raise ValueError("gute frame is truncated")
    flags = struct.unpack_from("<I", frame, 0x14)[0]
    if not flags & 1:
        return None
    length = (flags >> 1) & 0x7FFF
    if not length:
        raise ValueError("compressed gute frame has no original length")
    return length
