"""Bounded direct-media bootstrap for one already authenticated Yoosee route."""

from __future__ import annotations

import socket
import struct
import time
from dataclasses import dataclass

from .contracts import CallingResult, CertifiedNode, OnlineDevice
from .crypto import gute_mode1_decrypt
from .media_protocol import (
    build_media_meter_ack,
    build_media_meter_request,
    parse_media_meter,
)
from .rendezvous_protocol import build_direct_calling_request
from .session_io import local_route_ip, receive_datagrams


@dataclass(frozen=True, slots=True)
class MediaChannelResult:
    direct_acknowledged: bool
    meter_acknowledged: bool
    datagrams: int


def open_media_channel(
    sock: socket.socket,
    node: CertifiedNode,
    access_id: int,
    device: OnlineDevice,
    calling: CallingResult,
    timeout: float,
) -> MediaChannelResult:
    """Open only MTP routing/meter state; do not send AV or microphone frames."""

    peer = calling.peer_endpoint
    attempt = calling.attempt
    if not calling.direct_handshake or peer is None or attempt is None:
        return MediaChannelResult(False, False, 0)
    local_ip = local_route_ip(peer)
    local_port = sock.getsockname()[1]
    direct_a4 = build_direct_calling_request(
        node,
        access_id,
        device,
        local_ip,
        local_port,
        attempt,
        calling.next_sequence,
    )
    direct_acknowledged = False
    meter_acknowledged = False
    datagrams = 0
    bounded_timeout = max(0.1, min(float(timeout), 5.0))

    for meter_sequence in (1, 2):
        meter = build_media_meter_request(
            access_id,
            device.device_id,
            attempt.link_id,
            attempt.call_id,
            sequence=meter_sequence,
        )
        sock.sendto(meter, peer)
        sock.sendto(direct_a4, peer)
        for wire, source in receive_datagrams(sock, time.monotonic() + bounded_timeout):
            if source != peer:
                continue
            datagrams += 1
            if wire[:2] == b"\x7E\xA4":
                try:
                    response = gute_mode1_decrypt(wire)
                except ValueError:
                    continue
                if (
                    len(response) == 32
                    and struct.unpack_from("<I", response, 0x0C)[0] == calling.next_sequence
                    and struct.unpack_from("<I", response, 0x18)[0] == 4
                ):
                    direct_acknowledged = True
                continue
            if wire[:2] != b"\xC0\x90":
                continue
            parsed = parse_media_meter(wire)
            if (
                parsed is None
                or parsed.link_id != attempt.link_id
                or parsed.source_id != device.device_id
                or parsed.destination_id != access_id
            ):
                continue
            meter_acknowledged = True
            if parsed.kind == 1:
                sock.sendto(build_media_meter_ack(wire), peer)
        if direct_acknowledged and meter_acknowledged:
            break
    return MediaChannelResult(direct_acknowledged, meter_acknowledged, datagrams)
