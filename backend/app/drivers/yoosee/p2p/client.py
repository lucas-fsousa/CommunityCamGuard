"""Bounded IoTVideo access-node and rendezvous client.

The inventory probe stops after certification, account-device inventory, heartbeat and TermDNS.
The broad inspection surface is read-only. Typed feature writes live in isolated modules and this
transport exposes no public arbitrary-path writer or action constructor. Secrets and peer routes
never leave this module.

The wire format was reconstructed from the vendor Android SDK and validated in the ignored RE
laboratory.  Secrets are accepted as decoded values and never logged or included in results.
"""

from __future__ import annotations

import socket
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
from .access_session import LIST_HOST as LIST_HOST
from .access_session import LIST_PORT as LIST_PORT
from .access_session import certify_node as certify_node
from .access_session import establish_initialized_node as establish_initialized_node
from .access_session import heartbeat_node as heartbeat_node
from .access_session import initialize_node as initialize_node
from .access_session import obtain_list as obtain_list
from .access_session import resolve_term as resolve_term
from .contracts import (
    MODEL_READ_PATHS,
    CertifiedNode,
    LoginMaterial,
    OnlineDevice,
    P2PInventory,
    P2PProbeError,
    P2PPropertyRead,
    P2PRouteProbe,
)
from .contracts import CallingAttempt as CallingAttempt
from .contracts import CallingResult as CallingResult
from .contracts import ModelReadResult as ModelReadResult
from .contracts import ModelWriteResult as ModelWriteResult
from .model_protocol import build_model_read as build_model_read
from .model_protocol import parse_model_read_response as parse_model_read_response
from .model_protocol import parse_model_report as parse_model_report
from .model_session import exchange_model_read as exchange_model_read
from .rendezvous_protocol import build_calling_request as build_calling_request
from .rendezvous_protocol import build_nat_online as build_nat_online
from .rendezvous_protocol import build_nat_online_ack as build_nat_online_ack
from .rendezvous_protocol import parse_mtp_peer_endpoint as parse_mtp_peer_endpoint
from .rendezvous_session import call_device as call_device


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
