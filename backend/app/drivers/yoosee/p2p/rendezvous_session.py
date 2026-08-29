"""Bounded UDP orchestration for a direct Yoosee camera rendezvous."""

from __future__ import annotations

import secrets
import socket
import struct
import time

from .contracts import CallingAttempt, CallingResult, CertifiedNode, OnlineDevice
from .crypto import gute_mode0_decrypt
from .rendezvous_protocol import (
    build_calling_request,
    build_nat_online,
    build_nat_online_ack,
    parse_mtp_peer_endpoint,
)
from .session_io import (
    acknowledge_reliable_node_frame,
    decrypt_node_frame,
    local_route_ip,
    receive_datagrams,
)


def call_device(
    sock: socket.socket,
    node: CertifiedNode,
    access_id: int,
    device: OnlineDevice,
    timeout: float,
    *,
    retries: int = 4,
    interval: float = 3.0,
    deadline: float | None = None,
) -> CallingResult:
    """Broker and prove a direct NAT path without opening media or sending a command."""

    if retries < 1:
        raise ValueError("calling retries must be positive")
    attempt = CallingAttempt(
        link_id=secrets.randbelow(0xFFFFFF) + 1,
        call_id=secrets.randbits(32),
        cookie=secrets.token_bytes(8),
    )
    local_ip = local_route_ip(node.address)
    local_port = sock.getsockname()[1]
    node_acknowledged = False
    node_notified = False
    direct_datagrams = 0
    direct_handshake = False
    error_code = None
    peer_endpoint = None
    next_sequence = node.next_sequence
    nat_online = build_nat_online(access_id, device.device_id, attempt.link_id)
    nat_ack = build_nat_online_ack(access_id, attempt.link_id)

    for retry in range(retries):
        if deadline is not None and time.monotonic() >= deadline:
            break
        sequence = (node.next_sequence + retry) & 0xFFFFFFFF
        next_sequence = (sequence + 1) & 0xFFFFFFFF
        sock.sendto(
            build_calling_request(
                node,
                access_id,
                device,
                local_ip,
                local_port,
                attempt,
                sequence,
            ),
            node.address,
        )
        wait = timeout if retry + 1 == retries else max(timeout, interval)
        receive_until = time.monotonic() + wait
        if deadline is not None:
            receive_until = min(receive_until, deadline)
        for wire, peer in receive_datagrams(sock, receive_until):
            if peer != node.address:
                direct_datagrams += 1
                if len(wire) >= 0x20 and wire[:2] == b"\x7f\xca":
                    try:
                        direct = gute_mode0_decrypt(wire)
                    except ValueError:
                        continue
                    if (
                        len(direct) == 52
                        and struct.unpack_from("<I", direct, 0x24)[0] == attempt.link_id
                    ):
                        direct_handshake = True
                        sock.sendto(nat_online, peer)
                        sock.sendto(nat_ack, peer)
                continue
            plain = decrypt_node_frame(wire, node)
            if plain is None:
                continue
            acknowledge_reliable_node_frame(sock, node, plain)
            if plain[1] == 0xA4 and len(plain) >= 0x20:
                node_acknowledged = True
            elif plain[1] == 0xA3:
                node_notified = True
                candidate = parse_mtp_peer_endpoint(plain, attempt.link_id)
                if candidate is not None:
                    peer_endpoint = candidate
                    for _copy in range(3):
                        sock.sendto(nat_online, candidate)
            elif plain[1] == 0xA5 and len(plain) >= 0x36:
                error_code = struct.unpack_from("<H", plain, 0x34)[0]
        if direct_handshake:
            break
    return CallingResult(
        node_acknowledged=node_acknowledged,
        node_notified=node_notified,
        direct_datagrams=direct_datagrams,
        direct_handshake=direct_handshake,
        error_code=error_code,
        peer_endpoint=peer_endpoint,
        next_sequence=next_sequence,
    )
