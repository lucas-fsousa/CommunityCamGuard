"""One experimental native TCP route; caller holds camera and broker ownership."""

import ipaddress
import secrets
import select
import socket
import struct
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import cast

from ....db.p2p import P2PEnrollment
from .av_route_io import BudgetSocket
from .av_sample import AvVideoSample
from .broker_maintenance import BrokerMaintenance
from .camera_session import open_camera_session
from .contracts import CallingAttempt, P2PProbeError
from .crypto import gute_mode1_xor_checksum
from .media_protocol import build_av_init
from .mtp_relays import inspect_mtp_relay_advertisement
from .mtp_tcp_av import TcpAvSession
from .mtp_tcp_handshake import build_mtp_tcp_pair_request
from .mtp_tcp_receive import TcpReception, receive_paired_av
from .rendezvous_protocol import build_calling_request
from .rendezvous_session import close_device_route
from .session_io import local_route_ip, receive_datagrams
from .video_startup_645 import with_preconnect_definition


@dataclass(frozen=True, slots=True)
class TcpRouteResult:
    reception: TcpReception
    broker_release: bool
    broker_messages: int


def select_relay(frame: bytes, session_id: int, link_id: int) -> tuple[str, int] | None:
    """Experimental IPv4 TCP policy; input must already be broker-decrypted."""
    if len(frame) < 24 or gute_mode1_xor_checksum(frame) != struct.unpack_from("<I", frame, 16)[0]:
        return None
    advert = inspect_mtp_relay_advertisement(frame, expected_session_id=session_id,
                                           expected_link_id=link_id)
    if advert is None or any(entry.family != 4 for entry in advert.entries):
        return None
    # Opaque extension is not interpreted as an address, permission or platform.
    for entry in advert.entries:
        if entry.flags & 4 and entry.tcp_port and ipaddress.ip_address(entry.address).is_global:
            return entry.address, entry.tcp_port
    return None


def probe_tcp_route(enrollment: P2PEnrollment, *, camera_id: str, device_id: str,
                    sample: AvVideoSample | None = None,
                    cancelled: Callable[[], bool] = lambda: False) -> TcpRouteResult:
    """Fresh HD attempt only; no endpoint sweep, credential renewal or retry.

    Not a production source. Return separate media/CLOSE and broker-receipt facts.
    A false broker receipt remains unresolved, never promoted to remote cleanup.
    """
    if enrollment.camera_id != camera_id or enrollment.device_id != device_id:
        raise ValueError("TCP diagnostic enrollment mismatch")
    udp = BudgetSocket(socket.socket(socket.AF_INET, socket.SOCK_DGRAM), cancelled)
    datagrams = cast(socket.socket, udp)  # Same bounded facade as the direct-route diagnostic.
    tcp = None
    node = target = attempt = maintenance = owner = None
    calling_attempted = False
    release = False
    reception = None
    try:
        udp.bind(("", 0))
        node, target, _ = open_camera_session(datagrams, enrollment, 1.0, udp.deadline)
        if str(target.device_id) != device_id:
            raise ValueError("TCP diagnostic target mismatch")
        attempt = CallingAttempt(secrets.randbelow(0xFFFFFF) + 1,
                                 secrets.randbits(32), secrets.token_bytes(8))
        metadata = with_preconnect_definition(build_av_init(attempt.call_id)[24:56], 3)
        request = build_calling_request(node, enrollment.access_id, target,
            local_route_ip(node.address), udp.getsockname()[1], attempt, node.next_sequence,
            request_user_data=metadata, connection_type=1)
        maintenance = BrokerMaintenance(node)
        calling_attempted = True  # Includes ambiguous sends.
        udp.sendto(request, node.address)
        endpoint: tuple[str, int] | None = None

        def inspect_message(plain: bytes) -> None:
            nonlocal endpoint
            assert node is not None and attempt is not None
            endpoint = select_relay(plain, node.session_id, attempt.link_id)

        for wire, peer in receive_datagrams(datagrams, min(udp.deadline, time.monotonic() + 4)):
            for reply in maintenance.receive(wire, peer, inspect_message=inspect_message):
                udp.sendto(reply, node.address)
            if endpoint is not None:
                break
        if endpoint is None:
            raise P2PProbeError("no reviewed TCP relay advertisement")

        def service() -> None:
            assert node is not None and maintenance is not None
            for _ in range(4):
                if not select.select([udp.sock], [], [], 0)[0]:
                    break
                udp.settimeout(0.001)
                try:
                    wire, peer = udp.recvfrom(4096)
                except TimeoutError:
                    break
                for reply in maintenance.receive(wire, peer):
                    udp.sendto(reply, node.address)

        if cancelled():
            raise P2PProbeError("TCP diagnostic cancelled before connection")
        tcp = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        tcp.settimeout(min(1.0, udp.check()))
        tcp.connect(endpoint)
        tcp.settimeout(min(1.0, udp.check()))
        tcp.sendall(build_mtp_tcp_pair_request(relay_link_id=attempt.link_id,
            source_id=enrollment.access_id, destination_id=target.device_id,
            timestamp_ms=int(time.monotonic() * 1000)))
        owner = TcpAvSession(endpoint, attempt.link_id, attempt.call_id, attempt.cookie,
                            source_id=enrollment.access_id, destination_id=target.device_id,
                            request_user_data=metadata)
        reception = receive_paired_av(tcp, endpoint, owner, clock=time.monotonic,
            sample=sample, service_broker=service, cancelled=cancelled)
    except BaseException:
        if sample is not None:
            sample.close()
        raise
    finally:
        try:
            if calling_attempted and node is not None and target is not None and attempt is not None:
                udp.phase(1.5, cleanup=True)
                release = close_device_route(datagrams, node, enrollment.access_id, target,
                    attempt.link_id, (node.next_sequence + 1) & 0xFFFFFFFF, 1.0,
                    require_correlated_ack=True)
        except (OSError, ValueError, P2PProbeError):
            release = False
        finally:
            if owner is not None:
                owner.close()
            if maintenance is not None:
                maintenance.close()
            try:
                try:
                    if tcp is not None:
                        tcp.close()
                finally:
                    udp.close()
            except BaseException:
                if sample is not None:
                    sample.close()
                raise
    assert reception is not None and maintenance is not None
    return TcpRouteResult(reception, release, maintenance.messages)
