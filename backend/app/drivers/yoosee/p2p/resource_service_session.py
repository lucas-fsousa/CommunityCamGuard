"""Bounded session exchange for the read-only Yoosee alarm-resource catalogue."""

from __future__ import annotations

import socket
import struct
import time
from collections.abc import Callable
from dataclasses import dataclass

from .contracts import CertifiedNode
from .quicklz import decompress_level2
from .resource_service_protocol import (
    FragmentReassembler,
    build_alarm_voice_catalog_request,
    build_fragment_ack,
    compressed_gute_payload_length,
    decode_fragment_packet,
    parse_alarm_voice_catalog_response,
)
from .session_io import (
    acknowledge_reliable_node_frame,
    decrypt_node_frame,
    receive_datagrams,
)

PayloadDecompressor = Callable[[bytes, int], bytes]


@dataclass(frozen=True, slots=True)
class AlarmVoiceCatalogResult:
    """Transport result without retaining resource URLs or interpreting JSON metadata."""

    transport_acknowledged: bool
    status_code: int | None
    payload: bytes | None
    compression_required: bool
    fragments_received: int


def _expand_compressed_frame(
    frame: bytes,
    decompressor: PayloadDecompressor | None,
) -> tuple[bytes | None, bool]:
    expected_length = compressed_gute_payload_length(frame)
    if expected_length is None:
        return frame, False
    if decompressor is None:
        return None, True
    try:
        payload = decompressor(frame[0x18:], expected_length)
    except (TypeError, ValueError):
        return None, True
    if not isinstance(payload, bytes) or len(payload) != expected_length:
        return None, True
    expanded = bytearray(frame[:0x18])
    expanded.extend(payload)
    if len(expanded) > 0xFFFF:
        return None, True
    struct.pack_into("<H", expanded, 2, len(expanded))
    flags = struct.unpack_from("<I", expanded, 0x14)[0]
    struct.pack_into("<I", expanded, 0x14, flags & 0xFFFF0000)
    return bytes(expanded), False


def exchange_alarm_voice_catalog(
    sock: socket.socket,
    node: CertifiedNode,
    query: dict[str, object],
    sequence: int,
    timeout: float,
    *,
    retries: int = 3,
    deadline: float | None = None,
    decompressor: PayloadDecompressor | None = decompress_level2,
) -> AlarmVoiceCatalogResult:
    """Execute only the fixed read-only catalogue request against one authenticated node."""

    if retries < 1:
        raise ValueError("alarm-resource retries must be positive")
    if timeout <= 0:
        raise ValueError("alarm-resource timeout must be positive")
    request = build_alarm_voice_catalog_request(node, query, sequence)
    transport_acknowledged = False
    status_code = None
    payload = None
    compression_required = False
    fragments_received = 0
    fragments = FragmentReassembler()

    for _retry in range(retries):
        if deadline is not None and time.monotonic() >= deadline:
            break
        sock.sendto(request, node.address)
        receive_until = time.monotonic() + timeout
        if deadline is not None:
            receive_until = min(receive_until, deadline)
        for wire, peer in receive_datagrams(sock, receive_until):
            if peer != node.address:
                continue
            if wire[:2] == b"\x70\x01":
                try:
                    fragment = decode_fragment_packet(wire)
                    sock.sendto(build_fragment_ack(fragment), node.address)
                    fragments_received += 1
                    reassembled = fragments.add(fragment)
                except ValueError:
                    continue
                if reassembled is None:
                    continue
                wire = reassembled
            plain = decrypt_node_frame(wire, node)
            if plain is None or len(plain) < 0x18:
                continue
            flags = struct.unpack_from("<I", plain, 0x14)[0]
            if flags & (1 << 20):
                if plain[1] == 0xC0:
                    transport_acknowledged = True
                continue
            try:
                expanded, needs_decoder = _expand_compressed_frame(plain, decompressor)
            except ValueError:
                continue
            if needs_decoder:
                compression_required = True
                continue
            if expanded is None:
                continue
            parsed = parse_alarm_voice_catalog_response(expanded)
            acknowledge_reliable_node_frame(sock, node, expanded)
            if parsed is None:
                continue
            _incoming_id, status_code, payload = parsed
            break
        if status_code is not None or compression_required:
            break

    return AlarmVoiceCatalogResult(
        transport_acknowledged,
        status_code,
        payload,
        compression_required,
        fragments_received,
    )
