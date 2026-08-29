"""Bounded UDP lifecycle for Yoosee access-node sessions."""

from __future__ import annotations

import secrets
import socket
import struct
import time

from .access_protocol import (
    build_certification_ack,
    build_certification_request,
    build_heartbeat,
    build_init_info,
    build_list_query,
    build_mode2_response_ack,
    build_nat_probe,
    build_term_dns,
    parse_init_devices,
    parse_list_reply,
    parse_term_dns,
)
from .contracts import (
    CertifiedNode,
    InitInfoRejectedError,
    LoginMaterial,
    OnlineDevice,
    P2PProbeError,
)
from .crypto import gute_mode1_decrypt, gute_mode2_decrypt
from .session_io import (
    acknowledge_reliable_node_frame,
    decrypt_node_frame,
    local_route_ip,
    receive_datagrams,
)

LIST_HOST = "list.iotvideo.tencentcs.com"
LIST_PORT = 51701


def obtain_list(sock: socket.socket, access_id: int, timeout: float) -> list[tuple[str, int]]:
    try:
        hosts = {
            item[4][0]
            for item in socket.getaddrinfo(LIST_HOST, LIST_PORT, socket.AF_INET, socket.SOCK_DGRAM)
        }
    except OSError as exc:
        raise P2PProbeError("P2P list service could not be resolved") from exc
    query = build_list_query(access_id)
    for host in hosts:
        sock.sendto(query, (host, LIST_PORT))
    for wire, _peer in receive_datagrams(sock, time.monotonic() + timeout):
        if len(wire) >= 0x20 and wire[:2] == b"\x7f\x16":
            try:
                return parse_list_reply(wire)
            except ValueError:
                continue
    raise P2PProbeError("P2P list service did not answer")


def certify_node(
    sock: socket.socket,
    material: LoginMaterial,
    endpoints: list[tuple[str, int]],
    timeout: float,
    *,
    deadline: float | None = None,
) -> CertifiedNode:
    for endpoint in endpoints:
        if deadline is not None and time.monotonic() >= deadline:
            break
        sequence = secrets.randbits(32)
        sock.sendto(build_nat_probe(material.access_id, sequence), endpoint)
        receive_until = time.monotonic() + min(0.35, timeout)
        if deadline is not None:
            receive_until = min(receive_until, deadline)
        for wire, peer in receive_datagrams(sock, receive_until):
            if peer == endpoint and wire[:2] == b"\x7f\x02":
                break
        sequence = (sequence + 1) & 0xFFFFFFFF
        session_key = secrets.token_bytes(32)
        sock.sendto(build_certification_request(material, sequence, session_key), endpoint)
        response = None
        receive_until = time.monotonic() + timeout
        if deadline is not None:
            receive_until = min(receive_until, deadline)
        for wire, peer in receive_datagrams(sock, receive_until):
            if peer != endpoint or len(wire) < 0x20 or wire[0] != 0x7F:
                continue
            if (wire[0x16] & 3) != 1:
                continue
            try:
                plain = gute_mode1_decrypt(wire)
            except ValueError:
                continue
            if plain[1] == 0x0D and len(plain) >= 0x28:
                response = plain
                break
        if response is None or struct.unpack_from("<H", response, 0x1A)[0] != 0:
            continue
        session_id = struct.unpack_from("<Q", response, 0x1C)[0]
        if not session_id:
            continue
        sock.sendto(build_certification_ack(response), endpoint)
        return CertifiedNode(endpoint, session_id, session_key, (sequence + 1) & 0xFFFFFFFF)
    raise P2PProbeError("no advertised P2P node accepted certification")


def initialize_node(
    sock: socket.socket,
    node: CertifiedNode,
    timeout: float,
    *,
    retries: int = 3,
    deadline: float | None = None,
) -> tuple[CertifiedNode, tuple[OnlineDevice, ...]]:
    if retries < 1:
        raise ValueError("init-info retries must be positive")
    request = build_init_info(node)
    response = None
    for _attempt in range(retries):
        if deadline is not None and time.monotonic() >= deadline:
            break
        sock.sendto(request, node.address)
        receive_until = time.monotonic() + timeout
        if deadline is not None:
            receive_until = min(receive_until, deadline)
        for wire, peer in receive_datagrams(sock, receive_until):
            if peer != node.address or len(wire) < 0x1C or wire[0] != 0x7E:
                continue
            if (wire[0x16] & 3) != 2:
                continue
            try:
                plain = gute_mode2_decrypt(wire, node.session_key)
            except ValueError:
                continue
            if plain[1] == 0xA7 and len(plain) > 0x1B:
                response = plain
                acknowledge_reliable_node_frame(sock, node, plain)
                break
        if response is not None:
            break
    if response is None:
        raise P2PProbeError("certified P2P node did not return device inventory")
    error_code = struct.unpack_from("<H", response, 0x1A)[0]
    if error_code != 0:
        raise InitInfoRejectedError(error_code)
    devices = parse_init_devices(response)
    sock.sendto(build_mode2_response_ack(node, response), node.address)
    drain_until = time.monotonic() + min(timeout, 0.8)
    if deadline is not None:
        drain_until = min(drain_until, deadline)
    for wire, peer in receive_datagrams(sock, drain_until):
        if peer != node.address:
            continue
        trailing = decrypt_node_frame(wire, node)
        if trailing is not None:
            acknowledge_reliable_node_frame(sock, node, trailing)
    return (
        CertifiedNode(
            node.address,
            node.session_id,
            node.session_key,
            (node.next_sequence + 1) & 0xFFFFFFFF,
        ),
        devices,
    )


def establish_initialized_node(
    sock: socket.socket,
    material: LoginMaterial,
    endpoints: list[tuple[str, int]],
    timeout: float,
    *,
    deadline: float | None = None,
) -> tuple[CertifiedNode, tuple[OnlineDevice, ...], int]:
    remaining = list(endpoints)
    incomplete_nodes = 0
    while remaining:
        if deadline is not None and time.monotonic() >= deadline:
            raise P2PProbeError("P2P inventory probe exhausted its time budget")
        node = certify_node(sock, material, remaining, timeout, deadline=deadline)
        try:
            initialized, devices = initialize_node(sock, node, timeout, deadline=deadline)
        except InitInfoRejectedError:
            raise
        except P2PProbeError:
            incomplete_nodes += 1
            remaining = [endpoint for endpoint in remaining if endpoint != node.address]
            continue
        return initialized, devices, incomplete_nodes
    raise P2PProbeError("no certified P2P node completed initialization")


def heartbeat_node(sock: socket.socket, node: CertifiedNode, timeout: float) -> CertifiedNode:
    local_ip = local_route_ip(node.address)
    local_port = sock.getsockname()[1]
    sock.sendto(build_heartbeat(node, local_ip, local_port), node.address)
    for wire, peer in receive_datagrams(sock, time.monotonic() + timeout):
        if peer != node.address or len(wire) < 0x20 or wire[0] != 0x7E:
            continue
        if (wire[0x16] & 3) != 2:
            continue
        try:
            plain = gute_mode2_decrypt(wire, node.session_key)
        except ValueError:
            continue
        if plain[1] == 0xA1:
            return CertifiedNode(
                node.address,
                node.session_id,
                node.session_key,
                (node.next_sequence + 1) & 0xFFFFFFFF,
            )
        acknowledge_reliable_node_frame(sock, node, plain)
    raise P2PProbeError("P2P access node did not answer heartbeat")


def resolve_term(
    sock: socket.socket,
    node: CertifiedNode,
    term: str,
    timeout: float,
) -> bool:
    """Resolve one device term through the broker without opening a direct camera session."""

    sock.sendto(build_term_dns(node, term), node.address)
    for wire, peer in receive_datagrams(sock, time.monotonic() + timeout):
        if peer != node.address or len(wire) < 0x24 or wire[1] != 0xDC:
            continue
        try:
            _address, port = parse_term_dns(wire, node, term)
        except ValueError:
            continue
        plain = decrypt_node_frame(wire, node)
        if plain is not None:
            acknowledge_reliable_node_frame(sock, node, plain)
        return bool(port)
    return False
