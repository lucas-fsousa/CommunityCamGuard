"""Bounded IoTVideo access-node and rendezvous client.

The inventory probe stops after certification, account-device inventory, heartbeat and TermDNS.
The broad inspection surface is read-only. Typed feature writes live in isolated modules and this
transport exposes no public arbitrary-path writer or action constructor. Secrets and peer routes
never leave this module.

The wire format was reconstructed from the vendor Android SDK and validated in the ignored RE
laboratory.  Secrets are accepted as decoded values and never logged or included in results.
"""

from __future__ import annotations

import secrets
import socket
import struct
import time

from ....db.p2p import P2PEnrollment
from .access_protocol import build_certification_ack as build_certification_ack
from .access_protocol import build_certification_request as build_certification_request
from .access_protocol import build_heartbeat as build_heartbeat
from .access_protocol import build_init_info as build_init_info
from .access_protocol import build_list_query as build_list_query
from .access_protocol import build_mode1_response_ack as build_mode1_response_ack
from .access_protocol import build_mode2_response_ack as build_mode2_response_ack
from .access_protocol import build_nat_probe as build_nat_probe
from .access_protocol import build_term_dns as build_term_dns
from .access_protocol import parse_init_devices as parse_init_devices
from .access_protocol import parse_list_reply as parse_list_reply
from .access_protocol import parse_term_dns as parse_term_dns
from .contracts import (
    MODEL_READ_PATHS,
    CallingAttempt,
    CallingResult,
    CertifiedNode,
    InitInfoRejectedError,
    LoginMaterial,
    ModelReadResult,
    OnlineDevice,
    P2PInventory,
    P2PProbeError,
    P2PPropertyRead,
    P2PRouteProbe,
)
from .contracts import ModelWriteResult as ModelWriteResult
from .crypto import (
    gute_mode0_decrypt,
    gute_mode1_decrypt,
    gute_mode2_decrypt,
)
from .model_protocol import build_model_read as build_model_read
from .model_protocol import parse_model_read_response as parse_model_read_response
from .model_protocol import parse_model_report as parse_model_report
from .rendezvous_protocol import build_calling_request as build_calling_request
from .rendezvous_protocol import build_nat_online as build_nat_online
from .rendezvous_protocol import build_nat_online_ack as build_nat_online_ack
from .rendezvous_protocol import parse_mtp_peer_endpoint as parse_mtp_peer_endpoint
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
        for wire, peer in receive_datagrams(sock, receive_until):
            if peer != node.address:
                continue
            plain = decrypt_node_frame(wire, node)
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
