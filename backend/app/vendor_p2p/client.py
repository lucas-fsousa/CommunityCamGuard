"""Bounded IoTVideo access-node and rendezvous client.

The inventory probe stops after certification, account-device inventory, heartbeat and TermDNS.
The broad inspection surface is read-only. Typed feature writes live in isolated modules and this
transport exposes no public arbitrary-path writer or action constructor. Secrets and peer routes
never leave this module.

The wire format was reconstructed from the vendor Android SDK and validated in the ignored RE
laboratory.  Secrets are accepted as decoded values and never logged or included in results.
"""

from __future__ import annotations

import json
import secrets
import socket
import struct
import time
from dataclasses import dataclass

from ..db.p2p import P2PEnrollment
from .auth import build_conn_auth_blob
from .crypto import (
    RC5,
    gute_mode0_decrypt,
    gute_mode0_encrypt,
    gute_mode1_decrypt,
    gute_mode1_encrypt,
    gute_mode1_xor_checksum,
    gute_mode2_decrypt,
    gute_mode2_encrypt,
)

LIST_HOST = "list.iotvideo.tencentcs.com"
LIST_PORT = 51701

# Every entry is queried with the read-only B7 family. Action roots describe capabilities here;
# they are never invoked with the AC action family in this module.
MODEL_READ_PATHS = frozenset(
    {
        "ProConst._productInfo",
        "ProConst._versionInfo",
        "ProConst.devFuncCfg",
        "ProConst.devFunCode",
        "ProReadonly._online",
        "ProReadonly.sysVer",
        "ProReadonly.connectInfo",
        "ProReadonly.power",
        "ProReadonly.simCard",
        "ProReadonly.devInfo",
        "ProReadonly.tfInfo",
        "ProReadonly.aiModeDownL",
        "ProWritable._almEvtSetting",
        "ProWritable._otaMode",
        "ProWritable.timeZone",
        "ProWritable.onvifEn",
        "ProWritable.recordParm",
        "ProWritable.guardParm",
        "ProWritable.videoParm",
        "ProWritable.csVideoRes",
        "ProWritable.nightViewModeV2",
        "ProWritable.motionZone",
        "ProWritable.workMode",
        "ProWritable.pressKeyCall",
        "ProWritable.screenSwitch",
        "ProWritable.antiFlickerSwitch",
        "ProWritable.volume",
        "ProWritable.indicatorLight",
        "ProWritable.audioMode",
        "ProWritable.whiteLightPlan",
        "ProWritable.autoWhiteLight",
        "ProWritable.autoWorkMode",
        "ProWritable.resFile",
        "ProWritable.whiteLightCtrl",
        "ProWritable.zoomFocusW",
        "Action.whiteLightCtrl",
        "Action.expelCtrl",
        "Action.laserCtrl",
        "Action.ptzCheck",
        "Action.zoomFocusA",
    }
)


class P2PProbeError(RuntimeError):
    """Sanitized P2P failure safe to expose through the authenticated local API."""


class InitInfoRejectedError(P2PProbeError):
    def __init__(self, error_code: int):
        self.error_code = error_code
        label = "stale session" if error_code == 0x216B else "access rejected"
        super().__init__(f"P2P access node rejected initialization: {label}")


@dataclass(frozen=True, slots=True)
class LoginMaterial:
    access_id: int
    access_token: bytes


@dataclass(frozen=True, slots=True)
class CertifiedNode:
    address: tuple[str, int]
    session_id: int
    session_key: bytes
    next_sequence: int


@dataclass(frozen=True, slots=True)
class OnlineDevice:
    device_id: int
    status: int
    new_platform: bool
    server_id: int
    terminal_id: bytes


@dataclass(frozen=True, slots=True)
class P2PInventory:
    device_id: str
    authenticated: bool
    device_count: int
    online_count: int
    target_visible: bool
    target_online: bool
    target_term_resolved: bool
    skipped_incomplete_nodes: int


@dataclass(frozen=True, slots=True)
class CallingAttempt:
    link_id: int
    call_id: int
    cookie: bytes


@dataclass(frozen=True, slots=True)
class CallingResult:
    node_acknowledged: bool
    node_notified: bool
    direct_datagrams: int
    direct_handshake: bool
    error_code: int | None
    peer_endpoint: tuple[str, int] | None
    next_sequence: int = 0


@dataclass(frozen=True, slots=True)
class P2PRouteProbe:
    device_id: str
    authenticated: bool
    target_visible: bool
    target_online: bool
    broker_acknowledged: bool
    route_advertised: bool
    direct_datagrams: int
    direct_handshake: bool
    camera_contacted: bool
    broker_error_code: int | None


@dataclass(frozen=True, slots=True)
class ModelReadResult:
    transport_acknowledged: bool
    error_code: int | None
    value: object | None


@dataclass(frozen=True, slots=True)
class ModelWriteResult:
    transport_acknowledged: bool
    error_code: int | None


@dataclass(frozen=True, slots=True)
class P2PPropertyRead:
    device_id: str
    property_path: str
    authenticated: bool
    direct_handshake: bool
    transport_acknowledged: bool
    error_code: int | None
    value: object | None


def hash_string(data: bytes) -> int:
    value = 0x4E67C6A7
    for byte in data:
        value ^= ((value << 5) + byte + (value >> 2)) & 0xFFFFFFFF
        value &= 0xFFFFFFFF
    return value


def _new_header(subtype: int, length: int, identity: int, sequence: int, flags: int) -> bytearray:
    frame = bytearray(length)
    frame[0] = 0x7F
    frame[1] = subtype
    struct.pack_into("<H", frame, 2, length)
    struct.pack_into("<Q", frame, 4, identity & 0xFFFFFFFFFFFFFFFF)
    struct.pack_into("<I", frame, 0x0C, sequence & 0xFFFFFFFF)
    struct.pack_into("<I", frame, 0x14, flags & 0xFFFFFFFF)
    return frame


def _randomized_flags(*, mode: int, proc: int, extra: int = 0) -> int:
    return extra | ((secrets.randbits(15) & 0x7FFF) << 1) | ((mode & 3) << 16) | ((proc & 3) << 18)


def _finish_mode1(frame: bytearray) -> bytes:
    struct.pack_into("<I", frame, 0x10, gute_mode1_xor_checksum(frame))
    return gute_mode1_encrypt(bytes(frame))


def _finish_mode2(frame: bytearray, session_key: bytes) -> bytes:
    struct.pack_into("<I", frame, 0x10, gute_mode1_xor_checksum(frame))
    return gute_mode2_encrypt(bytes(frame), session_key)


def build_list_query(access_id: int) -> bytes:
    frame = _new_header(
        0x15, 40, access_id, secrets.randbits(32), _randomized_flags(mode=1, proc=0)
    )
    struct.pack_into("<I", frame, 0x18, 1)
    return _finish_mode1(frame)


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
    frame = _new_header(0x01, 68, access_id, sequence, _randomized_flags(mode=1, proc=0))
    struct.pack_into("<I", frame, 0x18, 2)
    struct.pack_into("<I", frame, 0x1C, int(time.monotonic() * 1000) & 0xFFFFFFFF)
    return _finish_mode1(frame)


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
    frame = _new_header(
        0x0C,
        0xA4,
        material.access_id,
        sequence,
        _randomized_flags(mode=1, proc=3, extra=(1 << 22) | (1 << 24)),
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
    frame = _new_header(
        0x0D,
        32,
        struct.unpack_from("<Q", response, 4)[0],
        struct.unpack_from("<I", response, 0x0C)[0],
        (1 << 20) | (1 << 16),
    )
    struct.pack_into("<I", frame, 0x18, 4)
    return _finish_mode1(frame)


def build_init_info(node: CertifiedNode) -> bytes:
    frame = _new_header(
        0xA6,
        62,
        node.session_id,
        node.next_sequence,
        _randomized_flags(mode=2, proc=3),
    )
    frame[0] = 0x7E
    struct.pack_into("<H", frame, 0x18, 0x3E)
    frame[0x1B] = 2
    struct.pack_into("<I", frame, 0x20, 0x28000000)
    frame[0x38:0x3E] = bytes.fromhex("000000000011")
    return _finish_mode2(frame, node.session_key)


def build_term_dns(node: CertifiedNode, term: str) -> bytes:
    encoded = term.encode("ascii")
    frame = _new_header(
        0xDB,
        0x1C + len(encoded) + 1,
        node.session_id,
        node.next_sequence,
        _randomized_flags(mode=2, proc=1),
    )
    frame[0] = 0x7E
    struct.pack_into("<H", frame, 0x18, len(encoded))
    frame[0x1C : 0x1C + len(encoded)] = encoded
    return _finish_mode2(frame, node.session_key)


def build_calling_request(
    node: CertifiedNode,
    access_id: int,
    device: OnlineDevice,
    local_ip: str,
    local_port: int,
    attempt: CallingAttempt,
    sequence: int,
) -> bytes:
    """Build the broker-facing A4 request without any control/media payload."""
    if len(attempt.cookie) != 8:
        raise ValueError("calling cookie must be eight bytes")
    if not 0 < attempt.link_id <= 0xFFFFFF:
        raise ValueError("calling link id must be a non-zero 24-bit value")
    frame = _new_header(
        0xA4,
        177,
        node.session_id,
        sequence,
        _randomized_flags(mode=2, proc=1),
    )
    frame[0] = 0x7E
    struct.pack_into("<H", frame, 0x18, 0x0581)
    struct.pack_into("<H", frame, 0x1A, local_port)
    struct.pack_into("<I", frame, 0x1C, attempt.link_id)
    struct.pack_into("<Q", frame, 0x20, access_id)
    struct.pack_into("<Q", frame, 0x28, device.device_id)
    frame[0x32] |= 1
    struct.pack_into("<H", frame, 0x36, local_port)
    frame[0x40:0x44] = socket.inet_aton(local_ip)
    frame[0x78:0x80] = attempt.cookie
    struct.pack_into("<I", frame, 0x84, attempt.call_id)
    struct.pack_into("<I", frame, 0x8C, 1)
    return _finish_mode2(frame, node.session_key)


def build_nat_online(access_id: int, device_id: int, link_id: int) -> bytes:
    """Build the clear-payload CA NAT-presence frame."""
    frame = _new_header(0xCA, 52, access_id, 0, 0)
    struct.pack_into("<Q", frame, 0x1C, device_id)
    struct.pack_into("<I", frame, 0x24, link_id)
    frame[0x29] = 3
    struct.pack_into("<I", frame, 0x10, gute_mode1_xor_checksum(frame))
    return gute_mode0_encrypt(bytes(frame))


def build_nat_online_ack(access_id: int, link_id: int) -> bytes:
    """Build the mode-1 CB acknowledgement for a matching CA."""
    frame = _new_header(0xCB, 36, access_id, 0, 1 << 16)
    struct.pack_into("<I", frame, 0x18, 4)
    struct.pack_into("<I", frame, 0x20, link_id)
    return _finish_mode1(frame)


def parse_mtp_peer_endpoint(response: bytes, link_id: int) -> tuple[str, int] | None:
    """Extract the camera's public endpoint from the broker A3 response."""
    if len(response) < 0x64 or response[1] != 0xA3:
        return None
    if struct.unpack_from("<I", response, 0x1C)[0] != link_id:
        return None
    port = struct.unpack_from(">H", response, 0x58)[0]
    address = response[0x60:0x64]
    if not port or address == b"\x00\x00\x00\x00":
        return None
    return socket.inet_ntoa(address), port


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


def build_model_read(
    node: CertifiedNode,
    device_id: int,
    path: str,
    sequence: int,
    message_id: int,
) -> bytes:
    """Build one allowlisted, read-only GDM B7 property request."""
    if path not in MODEL_READ_PATHS:
        raise ValueError("thing-model path is not in the read-only allowlist")
    encoded_path = path.encode("utf-8")
    frame = _new_header(
        0xB7,
        0x26 + len(encoded_path) + 1,
        node.session_id,
        sequence,
        _randomized_flags(mode=2, proc=3),
    )
    frame[0] = 0x7E
    struct.pack_into("<Q", frame, 0x18, device_id)
    struct.pack_into("<I", frame, 0x20, message_id & 0x7FFFFFFF)
    struct.pack_into("<H", frame, 0x24, len(encoded_path))
    frame[0x26 : 0x26 + len(encoded_path)] = encoded_path
    return _finish_mode2(frame, node.session_key)


def parse_model_read_response(frame: bytes, device_id: int) -> tuple[int, object | None] | None:
    """Parse direct B8 or access-node cached AA GDM responses."""
    if len(frame) < 0x26 or frame[1] not in (0xAA, 0xB8):
        return None
    if struct.unpack_from("<Q", frame, 0x18)[0] != device_id:
        return None
    error_code = struct.unpack_from("<H", frame, 0x24)[0]
    if not (frame[0x20] & 1):
        return error_code, None
    if len(frame) < 0x28:
        return None
    json_length = struct.unpack_from("<H", frame, 0x26)[0]
    if 0x28 + json_length > len(frame):
        return None
    try:
        value = json.loads(frame[0x28 : 0x28 + json_length].decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    return error_code, value


def parse_model_report(frame: bytes) -> tuple[int | None, str, object] | None:
    """Parse a brokered AA property report without accepting an action response."""
    if len(frame) < 0x22 or frame[1] != 0xAA:
        return None
    options = struct.unpack_from("<H", frame, 0x1C)[0]
    path_length = frame[0x1F] + 1
    json_length = struct.unpack_from("<H", frame, 0x20)[0] + 1
    cursor = 0x22
    destination = None
    if options & 1:
        if cursor + 8 > len(frame):
            return None
        destination = struct.unpack_from("<Q", frame, cursor)[0]
        cursor += 8
    if cursor + path_length + json_length > len(frame):
        return None
    encoded_path = frame[cursor : cursor + path_length].rstrip(b"\x00")
    cursor += path_length
    encoded_json = frame[cursor : cursor + json_length].rstrip(b"\x00")
    try:
        path = encoded_path.decode("utf-8")
        value = json.loads(encoded_json.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    return (destination, path, value) if path else None


def build_mode2_response_ack(node: CertifiedNode, response: bytes) -> bytes:
    frame = _new_header(
        response[1],
        32,
        node.session_id,
        struct.unpack_from("<I", response, 0x0C)[0],
        (1 << 20) | (2 << 16),
    )
    frame[0] = 0x7E
    struct.pack_into("<I", frame, 0x18, 4)
    return _finish_mode2(frame, node.session_key)


def build_mode1_response_ack(node: CertifiedNode, response: bytes) -> bytes:
    response_flags = struct.unpack_from("<I", response, 0x14)[0]
    frame = _new_header(
        response[1],
        32,
        node.session_id,
        struct.unpack_from("<I", response, 0x0C)[0],
        (1 << 20) | (1 << 16) | (response_flags & (1 << 25)),
    )
    frame[0] = 0x7E
    struct.pack_into("<I", frame, 0x18, 4)
    return _finish_mode1(frame)


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


def local_route_ip(peer: tuple[str, int]) -> str:
    route = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        route.connect(peer)
        return route.getsockname()[0]
    finally:
        route.close()


def build_heartbeat(node: CertifiedNode, local_ip: str, local_port: int) -> bytes:
    frame = _new_header(
        0xA0,
        48,
        node.session_id,
        node.next_sequence,
        _randomized_flags(mode=2, proc=2),
    )
    frame[0] = 0x7E
    struct.pack_into("<H", frame, 0x18, 2)
    struct.pack_into("<H", frame, 0x1A, local_port)
    frame[0x1C:0x20] = socket.inet_aton(local_ip)
    frame[0x21] = 1
    struct.pack_into("<I", frame, 0x2C, 1)
    return _finish_mode2(frame, node.session_key)


def _decrypt_node_frame(wire: bytes, node: CertifiedNode) -> bytes | None:
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


def _receive(sock: socket.socket, deadline: float):
    while time.monotonic() < deadline:
        sock.settimeout(max(0.05, deadline - time.monotonic()))
        try:
            yield sock.recvfrom(4096)
        except TimeoutError:
            return


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
    for wire, _peer in _receive(sock, time.monotonic() + timeout):
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
        for wire, peer in _receive(sock, receive_until):
            if peer == endpoint and wire[:2] == b"\x7f\x02":
                break
        sequence = (sequence + 1) & 0xFFFFFFFF
        session_key = secrets.token_bytes(32)
        sock.sendto(build_certification_request(material, sequence, session_key), endpoint)
        response = None
        receive_until = time.monotonic() + timeout
        if deadline is not None:
            receive_until = min(receive_until, deadline)
        for wire, peer in _receive(sock, receive_until):
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
        for wire, peer in _receive(sock, receive_until):
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
    for wire, peer in _receive(sock, drain_until):
        if peer != node.address:
            continue
        trailing = _decrypt_node_frame(wire, node)
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
    for wire, peer in _receive(sock, time.monotonic() + timeout):
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
    for wire, peer in _receive(sock, time.monotonic() + timeout):
        if peer != node.address or len(wire) < 0x24 or wire[1] != 0xDC:
            continue
        try:
            _address, port = parse_term_dns(wire, node, term)
        except ValueError:
            continue
        plain = _decrypt_node_frame(wire, node)
        if plain is not None:
            acknowledge_reliable_node_frame(sock, node, plain)
        return bool(port)
    return False


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
        for wire, peer in _receive(sock, receive_until):
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
            plain = _decrypt_node_frame(wire, node)
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


def exchange_model_read(
    sock: socket.socket,
    node: CertifiedNode,
    device: OnlineDevice,
    path: str,
    sequence: int,
    timeout: float,
    *,
    retries: int = 3,
    deadline: float | None = None,
) -> ModelReadResult:
    """Read one allowlisted property; this function cannot construct writes or actions."""
    if retries < 1:
        raise ValueError("model-read retries must be positive")
    request = build_model_read(node, device.device_id, path, sequence, secrets.randbits(31))
    transport_acknowledged = False
    error_code = None
    value = None
    for _retry in range(retries):
        if deadline is not None and time.monotonic() >= deadline:
            break
        sock.sendto(request, node.address)
        receive_until = time.monotonic() + timeout
        if deadline is not None:
            receive_until = min(receive_until, deadline)
        for wire, peer in _receive(sock, receive_until):
            if peer != node.address:
                continue
            plain = _decrypt_node_frame(wire, node)
            if plain is None:
                continue
            flags = struct.unpack_from("<I", plain, 0x14)[0]
            if flags & (1 << 20):
                if plain[1] == 0xB7:
                    transport_acknowledged = True
                continue
            report = parse_model_report(plain)
            if report is not None:
                destination, report_path, report_value = report
                acknowledge_reliable_node_frame(sock, node, plain)
                if destination is not None and destination != device.device_id:
                    continue
                if (
                    report_path == path
                    or report_path.startswith(path + ".")
                    or path.startswith(report_path + ".")
                ):
                    error_code, value = 0, report_value
                    break
                continue
            parsed = parse_model_read_response(plain, device.device_id)
            acknowledge_reliable_node_frame(sock, node, plain)
            if parsed is not None:
                error_code, value = parsed
                break
        if error_code is not None:
            break
    return ModelReadResult(transport_acknowledged, error_code, value)


def _camera_session(
    sock: socket.socket,
    enrollment: P2PEnrollment,
    timeout: float,
    deadline: float,
) -> tuple[CertifiedNode, OnlineDevice, int]:
    """Open one initialized route to exactly the durable enrollment's camera."""

    material = LoginMaterial(enrollment.access_id, enrollment.access_token)
    endpoints = obtain_list(sock, material.access_id, timeout)
    endpoints.sort(key=lambda endpoint: endpoint[1] != 19800)
    node, devices, _skipped = establish_initialized_node(
        sock,
        material,
        endpoints[:8],
        timeout,
        deadline=deadline,
    )
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise P2PProbeError("P2P camera session exhausted its time budget")
    node = heartbeat_node(sock, node, min(timeout, remaining))
    target = next(
        (device for device in devices if str(device.device_id) == enrollment.device_id),
        None,
    )
    if target is None or not target.status:
        raise P2PProbeError("selected P2P camera is not online")
    calling = call_device(
        sock,
        node,
        material.access_id,
        target,
        timeout,
        deadline=deadline,
    )
    if not calling.direct_handshake:
        raise P2PProbeError("selected P2P camera did not complete the direct handshake")
    return node, target, calling.next_sequence


def read_camera_property(
    enrollment: P2PEnrollment,
    property_path: str,
    *,
    timeout: float = 1.5,
    total_timeout: float = 25.0,
) -> P2PPropertyRead:
    """Open only the selected target route and perform one allowlisted B7 read."""
    if property_path not in MODEL_READ_PATHS:
        raise P2PProbeError("thing-model path is not in the read-only allowlist")
    bounded_timeout = max(0.5, min(float(timeout), 5.0))
    deadline = time.monotonic() + max(8.0, min(float(total_timeout), 35.0))
    material = LoginMaterial(enrollment.access_id, enrollment.access_token)
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("", 0))
    try:
        endpoints = obtain_list(sock, material.access_id, bounded_timeout)
        endpoints.sort(key=lambda endpoint: endpoint[1] != 19800)
        node, devices, _skipped = establish_initialized_node(
            sock,
            material,
            endpoints[:8],
            bounded_timeout,
            deadline=deadline,
        )
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise P2PProbeError("P2P property read exhausted its time budget")
        node = heartbeat_node(sock, node, min(bounded_timeout, remaining))
        target = next(
            (device for device in devices if str(device.device_id) == enrollment.device_id),
            None,
        )
        if target is None or not target.status:
            raise P2PProbeError("selected P2P camera is not online")
        calling = call_device(
            sock,
            node,
            material.access_id,
            target,
            bounded_timeout,
            deadline=deadline,
        )
        if not calling.direct_handshake:
            raise P2PProbeError("selected P2P camera did not complete the direct handshake")
        model = exchange_model_read(
            sock,
            node,
            target,
            property_path,
            calling.next_sequence,
            min(5.0, max(0.5, deadline - time.monotonic())),
            deadline=deadline,
        )
    except P2PProbeError:
        raise
    except (OSError, ValueError) as exc:
        raise P2PProbeError("P2P property read failed") from exc
    finally:
        sock.close()
    return P2PPropertyRead(
        device_id=enrollment.device_id,
        property_path=property_path,
        authenticated=True,
        direct_handshake=True,
        transport_acknowledged=model.transport_acknowledged,
        error_code=model.error_code,
        value=model.value,
    )


def probe_camera_route(
    enrollment: P2PEnrollment,
    *,
    timeout: float = 1.5,
    total_timeout: float = 20.0,
) -> P2PRouteProbe:
    """Authenticate and prove the selected camera's P2P route without application I/O."""
    bounded_timeout = max(0.5, min(float(timeout), 5.0))
    deadline = time.monotonic() + max(5.0, min(float(total_timeout), 30.0))
    material = LoginMaterial(enrollment.access_id, enrollment.access_token)
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("", 0))
    try:
        endpoints = obtain_list(sock, material.access_id, bounded_timeout)
        endpoints.sort(key=lambda endpoint: endpoint[1] != 19800)
        node, devices, _skipped = establish_initialized_node(
            sock,
            material,
            endpoints[:8],
            bounded_timeout,
            deadline=deadline,
        )
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise P2PProbeError("P2P route probe exhausted its time budget")
        node = heartbeat_node(sock, node, min(bounded_timeout, remaining))
        target = next(
            (device for device in devices if str(device.device_id) == enrollment.device_id),
            None,
        )
        if target is None:
            return P2PRouteProbe(
                enrollment.device_id, True, False, False, False, False, 0, False, False, None
            )
        if not target.status:
            return P2PRouteProbe(
                enrollment.device_id, True, True, False, False, False, 0, False, False, None
            )
        result = call_device(
            sock,
            node,
            material.access_id,
            target,
            bounded_timeout,
            deadline=deadline,
        )
    except P2PProbeError:
        raise
    except (OSError, ValueError) as exc:
        raise P2PProbeError("P2P camera route probe failed") from exc
    finally:
        sock.close()
    return P2PRouteProbe(
        device_id=enrollment.device_id,
        authenticated=True,
        target_visible=True,
        target_online=True,
        broker_acknowledged=result.node_acknowledged,
        route_advertised=result.node_notified,
        direct_datagrams=result.direct_datagrams,
        direct_handshake=result.direct_handshake,
        camera_contacted=result.direct_handshake,
        broker_error_code=result.error_code,
    )


def probe_account_inventory(
    enrollment: P2PEnrollment,
    *,
    timeout: float = 1.5,
    total_timeout: float = 15.0,
) -> P2PInventory:
    """Authenticate and inspect account inventory without contacting a camera directly."""
    bounded_timeout = max(0.5, min(float(timeout), 5.0))
    deadline = time.monotonic() + max(3.0, min(float(total_timeout), 20.0))
    material = LoginMaterial(enrollment.access_id, enrollment.access_token)
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("", 0))
    try:
        endpoints = obtain_list(sock, material.access_id, bounded_timeout)
        # Port 19800 is the access/message service used by the native client. Other advertised
        # ports may certify but not complete initialization.
        endpoints.sort(key=lambda endpoint: endpoint[1] != 19800)
        # Brokers may publish dozens of historical ports. Bound both the candidates and the
        # whole operation so this synchronous API can never monopolize a worker indefinitely.
        endpoints = endpoints[:8]
        node, devices, skipped = establish_initialized_node(
            sock, material, endpoints, bounded_timeout, deadline=deadline
        )
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise P2PProbeError("P2P inventory probe exhausted its time budget")
        node = heartbeat_node(sock, node, min(bounded_timeout, remaining))
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise P2PProbeError("P2P inventory probe exhausted its time budget")
        term_resolved = resolve_term(
            sock, node, enrollment.device_id, min(bounded_timeout, remaining)
        )
    except P2PProbeError:
        raise
    except (OSError, ValueError) as exc:
        raise P2PProbeError("P2P inventory probe failed") from exc
    finally:
        sock.close()
    target = next(
        (device for device in devices if str(device.device_id) == enrollment.device_id), None
    )
    return P2PInventory(
        device_id=enrollment.device_id,
        authenticated=True,
        device_count=len(devices),
        online_count=sum(1 for device in devices if device.status),
        target_visible=target is not None,
        target_online=bool(target and target.status),
        target_term_resolved=term_resolved,
        skipped_incomplete_nodes=skipped,
    )
