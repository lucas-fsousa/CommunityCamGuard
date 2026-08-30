"""Bounded AV initialization over an already metered Yoosee media channel."""

from __future__ import annotations

import socket
import struct
import time
from dataclasses import dataclass

from .contracts import CallingResult
from .media_protocol import (
    KCP_ACK,
    KCP_PUSH,
    build_av_init,
    build_kcp_ack,
    build_kcp_push,
    parse_kcp_segments,
)
from .session_io import receive_datagrams
from .stream_protocol import V1EncodingHeader, decrypt_media_tlv, unpack_v1_encoding_header


@dataclass(frozen=True, slots=True)
class AvSessionResult:
    kcp_ack_count: int
    actions: tuple[int, ...]
    bulk_frames: int
    next_send_sequence: int
    inbound_next: tuple[tuple[int, int], ...]
    stream_version: int | None
    encoding_header: V1EncodingHeader | None

    @property
    def accepted(self) -> bool:
        return self.kcp_ack_count > 0 and any(action in (2, 6) for action in self.actions)


def initialize_av_session(
    sock: socket.socket,
    calling: CallingResult,
    timeout: float,
) -> AvSessionResult:
    """Send four bounded AV INIT attempts and ACK camera pushes without opening talk."""

    attempt = calling.attempt
    peer = calling.peer_endpoint
    if attempt is None or peer is None:
        return AvSessionResult(0, (), 0, 0, (), None, None)
    conv = attempt.link_id | 0x80000000
    init = build_av_init(attempt.call_id)
    kcp_ack_count = 0
    actions: list[int] = []
    bulk_frames = 0
    inbound_next: dict[int, int] = {}
    stream_version: int | None = None
    encoding_header: V1EncodingHeader | None = None
    bounded_timeout = max(0.1, min(float(timeout), 5.0))

    for sequence in range(4):
        sock.sendto(build_kcp_push(conv, sequence, init), peer)
        for wire, source in receive_datagrams(
            sock, time.monotonic() + min(bounded_timeout, 0.25)
        ):
            if source != peer or wire[:2] != b"\xC0\x10":
                continue
            try:
                segments = parse_kcp_segments(wire)
            except ValueError:
                continue
            for segment in segments:
                if segment.command == KCP_ACK and segment.conv == conv:
                    kcp_ack_count += 1
                    continue
                if segment.command != KCP_PUSH:
                    continue
                inbound_next[segment.conv] = max(
                    inbound_next.get(segment.conv, 0), segment.sequence + 1
                )
                sock.sendto(
                    build_kcp_ack(
                        segment.conv,
                        segment.sequence,
                        segment.timestamp,
                        unacknowledged=segment.sequence + 1,
                    ),
                    peer,
                )
                body = segment.body
                if len(body) >= 12 and body[0] == 3:
                    action = struct.unpack_from("<I", body, 8)[0]
                    if action not in actions:
                        actions.append(action)
                    continue
                if not body or body[0] != 4:
                    continue
                bulk_frames += 1
                try:
                    payload = decrypt_media_tlv(body, attempt.cookie)
                except ValueError:
                    continue
                if stream_version is None:
                    if payload.startswith(bytes.fromhex("ffffff88")):
                        stream_version = 1
                    elif len(payload) >= 2 and struct.unpack_from("<H", payload)[0] & 0xFF80 == 0x80:
                        stream_version = 2
                if encoding_header is None and len(payload) >= 28:
                    try:
                        encoding_header = unpack_v1_encoding_header(payload[:28])
                    except ValueError:
                        pass
    return AvSessionResult(
        kcp_ack_count,
        tuple(actions),
        bulk_frames,
        4,
        tuple(sorted(inbound_next.items())),
        stream_version,
        encoding_header,
    )
