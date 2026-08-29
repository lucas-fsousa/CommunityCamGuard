"""Access-node discovery, certification and session-maintenance frame codec."""

from __future__ import annotations

import secrets
import socket
import struct
import time

from .auth import build_conn_auth_blob
from .contracts import CertifiedNode, LoginMaterial, OnlineDevice
from .crypto import (
    RC5,
    gute_mode1_decrypt,
    gute_mode1_encrypt,
    gute_mode1_xor_checksum,
    gute_mode2_decrypt,
)
from .wire import finish_mode1, finish_mode2, hash_string, new_header, randomized_flags


def build_list_query(access_id: int) -> bytes:
    frame = new_header(0x15, 40, access_id, secrets.randbits(32), randomized_flags(mode=1, proc=0))
    struct.pack_into("<I", frame, 0x18, 1)
    return finish_mode1(frame)


def parse_list_reply(wire: bytes) -> list[tuple[str, int]]:
    frame = gute_mode1_decrypt(wire)
    if frame[:2] != b"\x7f\x16" or len(frame) < 0x20:
        raise ValueError("not a modern list response")
    count = frame[0x1E]
    if len(frame) != 0x20 + count * 0x24:
        raise ValueError("invalid modern list record layout")
    endpoints: list[tuple[str, int]] = []
    for index in range(count):
        record = frame[0x20 + index * 0x24 : 0x20 + (index + 1) * 0x24]
        host = socket.inet_ntoa(record[:4])
        for offset in (0x18, 0x1A, 0x1C, 0x1E):
            port = struct.unpack_from(">H", record, offset)[0]
            endpoint = (host, port)
            if port and endpoint not in endpoints:
                endpoints.append(endpoint)
    return endpoints


def build_nat_probe(access_id: int, sequence: int) -> bytes:
    frame = new_header(0x01, 68, access_id, sequence, randomized_flags(mode=1, proc=0))
    struct.pack_into("<I", frame, 0x18, 2)
    struct.pack_into("<I", frame, 0x1C, int(time.monotonic() * 1000) & 0xFFFFFFFF)
    return finish_mode1(frame)


def _wrap_certification_key(session_key: bytes, access_token: bytes) -> bytes:
    rc5 = RC5(access_token[48:64], rounds=6, w=64)
    return b"".join(
        rc5.encrypt_block(session_key[offset : offset + 16])
        for offset in range(0, len(session_key), 16)
    )


def build_certification_request(
    material: LoginMaterial,
    sequence: int,
    session_key: bytes,
    *,
    mtu: int = 556,
) -> bytes:
    if len(session_key) != 32:
        raise ValueError("certification key must be 32 bytes")
    frame = new_header(
        0x0C,
        0xA4,
        material.access_id,
        sequence,
        randomized_flags(mode=1, proc=3, extra=(1 << 22) | (1 << 24)),
    )
    struct.pack_into("<H", frame, 0x18, 1)
    struct.pack_into("<I", frame, 0x1C, hash_string(session_key))
    frame[0x20:0x40] = _wrap_certification_key(session_key, material.access_token)
    struct.pack_into("<I", frame, 0x40, mtu)
    struct.pack_into("<Q", frame, 0x44, int(time.monotonic() * 1000))
    struct.pack_into("<I", frame, 0x10, gute_mode1_xor_checksum(frame))
    frame[0x54:0xA4] = build_conn_auth_blob(material.access_token, bytes(frame[0x0C:0x14]))
    return gute_mode1_encrypt(bytes(frame))


def build_certification_ack(response: bytes) -> bytes:
    frame = new_header(
        0x0D,
        32,
        struct.unpack_from("<Q", response, 4)[0],
        struct.unpack_from("<I", response, 0x0C)[0],
        (1 << 20) | (1 << 16),
    )
    struct.pack_into("<I", frame, 0x18, 4)
    return finish_mode1(frame)


def build_init_info(node: CertifiedNode) -> bytes:
    frame = new_header(
        0xA6,
        62,
        node.session_id,
        node.next_sequence,
        randomized_flags(mode=2, proc=3),
    )
    frame[0] = 0x7E
    struct.pack_into("<H", frame, 0x18, 0x3E)
    frame[0x1B] = 2
    struct.pack_into("<I", frame, 0x20, 0x28000000)
    frame[0x38:0x3E] = bytes.fromhex("000000000011")
    return finish_mode2(frame, node.session_key)


def build_term_dns(node: CertifiedNode, term: str) -> bytes:
    encoded = term.encode("ascii")
    frame = new_header(
        0xDB,
        0x1C + len(encoded) + 1,
        node.session_id,
        node.next_sequence,
        randomized_flags(mode=2, proc=1),
    )
    frame[0] = 0x7E
    struct.pack_into("<H", frame, 0x18, len(encoded))
    frame[0x1C : 0x1C + len(encoded)] = encoded
    return finish_mode2(frame, node.session_key)


def parse_term_dns(wire: bytes, node: CertifiedNode, expected_term: str) -> tuple[bytes, int]:
    mode = wire[0x16] & 3
    if mode == 2:
        frame = gute_mode2_decrypt(wire, node.session_key)
    elif mode == 1:
        frame = gute_mode1_decrypt(wire)
    else:
        frame = wire
    if len(frame) < 0x24 or frame[1] != 0xDC:
        raise ValueError("not a TermDNS response")
    domain_len = struct.unpack_from("<H", frame, 0x18)[0]
    domain = frame[0x24 : 0x24 + domain_len].rstrip(b"\x00").decode("ascii")
    if domain != expected_term:
        raise ValueError("TermDNS response does not match the requested term")
    return frame[0x1C:0x20], struct.unpack_from("<I", frame, 0x20)[0]


def build_mode2_response_ack(node: CertifiedNode, response: bytes) -> bytes:
    frame = new_header(
        response[1],
        32,
        node.session_id,
        struct.unpack_from("<I", response, 0x0C)[0],
        (1 << 20) | (2 << 16),
    )
    frame[0] = 0x7E
    struct.pack_into("<I", frame, 0x18, 4)
    return finish_mode2(frame, node.session_key)


def build_mode1_response_ack(node: CertifiedNode, response: bytes) -> bytes:
    response_flags = struct.unpack_from("<I", response, 0x14)[0]
    frame = new_header(
        response[1],
        32,
        node.session_id,
        struct.unpack_from("<I", response, 0x0C)[0],
        (1 << 20) | (1 << 16) | (response_flags & (1 << 25)),
    )
    frame[0] = 0x7E
    struct.pack_into("<I", frame, 0x18, 4)
    return finish_mode1(frame)


def parse_init_devices(response: bytes) -> tuple[OnlineDevice, ...]:
    options = struct.unpack_from("<H", response, 0x18)[0]
    if not (options & 1):
        return ()
    offline_count, online_count = struct.unpack_from("<HH", response, 0x1C)
    total = offline_count + online_count
    offset = 0x20
    if offset + total * 0x1C > len(response):
        raise ValueError("truncated init-info device table")
    devices = []
    for index in range(total):
        record = response[offset + index * 0x1C : offset + (index + 1) * 0x1C]
        devices.append(
            OnlineDevice(
                device_id=struct.unpack_from("<Q", record, 0)[0],
                status=record[8],
                new_platform=bool(record[9] & 1),
                server_id=struct.unpack_from("<H", record, 0x0A)[0],
                terminal_id=record[0x0C:0x1C],
            )
        )
    return tuple(devices)


def build_heartbeat(node: CertifiedNode, local_ip: str, local_port: int) -> bytes:
    frame = new_header(
        0xA0,
        48,
        node.session_id,
        node.next_sequence,
        randomized_flags(mode=2, proc=2),
    )
    frame[0] = 0x7E
    struct.pack_into("<H", frame, 0x18, 2)
    struct.pack_into("<H", frame, 0x1A, local_port)
    frame[0x1C:0x20] = socket.inet_aton(local_ip)
    frame[0x21] = 1
    struct.pack_into("<I", frame, 0x2C, 1)
    return finish_mode2(frame, node.session_key)
