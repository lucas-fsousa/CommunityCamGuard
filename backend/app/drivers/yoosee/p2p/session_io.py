"""Shared UDP receive, frame decryption and acknowledgement mechanics."""

from __future__ import annotations

import socket
import struct
import time
from collections.abc import Iterator

from .access_protocol import build_mode1_response_ack, build_mode2_response_ack
from .contracts import CertifiedNode
from .crypto import gute_mode1_decrypt, gute_mode2_decrypt


def local_route_ip(peer: tuple[str, int]) -> str:
    """Return the local IPv4 address selected by the kernel for a UDP peer."""

    route = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        route.connect(peer)
        return route.getsockname()[0]
    finally:
        route.close()


def decrypt_node_frame(wire: bytes, node: CertifiedNode) -> bytes | None:
    """Decrypt one supported access-node frame, returning ``None`` when invalid."""

    if len(wire) < 0x20 or wire[0] not in (0x7E, 0x7F):
        return None
    mode = wire[0x16] & 3
    try:
        if mode == 2:
            return gute_mode2_decrypt(wire, node.session_key)
        if mode == 1:
            return gute_mode1_decrypt(wire)
    except ValueError:
        return None
    return bytes(wire) if mode == 0 else None


def acknowledge_reliable_node_frame(sock: socket.socket, node: CertifiedNode, frame: bytes) -> bool:
    """Acknowledge one reliable access-node frame when its encryption mode is supported."""

    flags = struct.unpack_from("<I", frame, 0x14)[0]
    if flags & (1 << 20) or ((flags >> 18) & 3) == 0:
        return False
    mode = (flags >> 16) & 3
    if mode == 2:
        ack = build_mode2_response_ack(node, frame)
    elif mode == 1:
        ack = build_mode1_response_ack(node, frame)
    else:
        return False
    sock.sendto(ack, node.address)
    return True


def receive_datagrams(
    sock: socket.socket, deadline: float
) -> Iterator[tuple[bytes, tuple[str, int]]]:
    """Yield UDP datagrams until an absolute monotonic deadline expires."""

    while time.monotonic() < deadline:
        sock.settimeout(max(0.05, deadline - time.monotonic()))
        try:
            yield sock.recvfrom(4096)
        except TimeoutError:
            return
