"""Direct-camera rendezvous frame codec for the Yoosee IoTVideo transport."""

from __future__ import annotations

import secrets
import socket
import struct

from .contracts import CallingAttempt, CertifiedNode, OnlineDevice
from .crypto import gute_mode0_encrypt, gute_mode1_xor_checksum
from .wire import finish_mode1, finish_mode2, new_header, randomized_flags


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
    frame = new_header(
        0xA4,
        177,
        node.session_id,
        sequence,
        randomized_flags(mode=2, proc=1),
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
    return finish_mode2(frame, node.session_key)


def build_direct_calling_request(
    node: CertifiedNode,
    access_id: int,
    device: OnlineDevice,
    local_ip: str,
    local_port: int,
    attempt: CallingAttempt,
    sequence: int,
) -> bytes:
    """Build the camera-facing mode-1 A4 that opens the direct media channel."""

    if len(attempt.cookie) != 8:
        raise ValueError("calling cookie must be eight bytes")
    if not 0 < attempt.link_id <= 0xFFFFFF:
        raise ValueError("calling link id must be a non-zero 24-bit value")
    frame = new_header(
        0xA4,
        177,
        node.session_id,
        sequence,
        randomized_flags(mode=1, proc=1),
    )
    frame[0] = 0x7E
    struct.pack_into("<H", frame, 0x18, 0x4483)
    struct.pack_into("<I", frame, 0x1C, attempt.link_id)
    struct.pack_into("<Q", frame, 0x20, access_id)
    struct.pack_into("<Q", frame, 0x28, device.device_id)
    frame[0x32] = 1
    struct.pack_into("<H", frame, 0x36, local_port)
    frame[0x40:0x44] = socket.inet_aton(local_ip)
    frame[0x78:0x80] = attempt.cookie
    struct.pack_into("<I", frame, 0x84, attempt.call_id)
    struct.pack_into("<I", frame, 0x88, 1)
    struct.pack_into("<I", frame, 0x8C, 1)
    struct.pack_into("<I", frame, 0x90, 1)
    frame[0xA7] = 0x12
    frame[0xB0] = 1
    return finish_mode1(frame)


def build_nat_online(access_id: int, device_id: int, link_id: int) -> bytes:
    """Build the clear-payload CA NAT-presence frame."""
    frame = new_header(0xCA, 52, access_id, 0, 0)
    struct.pack_into("<Q", frame, 0x1C, device_id)
    struct.pack_into("<I", frame, 0x24, link_id)
    frame[0x29] = 3
    struct.pack_into("<I", frame, 0x10, gute_mode1_xor_checksum(frame))
    return gute_mode0_encrypt(bytes(frame))


def build_nat_online_ack(access_id: int, link_id: int) -> bytes:
    """Build the mode-1 CB acknowledgement for a matching CA."""
    frame = new_header(0xCB, 36, access_id, 0, 1 << 16)
    struct.pack_into("<I", frame, 0x18, 4)
    struct.pack_into("<I", frame, 0x20, link_id)
    return finish_mode1(frame)


def build_route_hangup(
    node: CertifiedNode,
    access_id: int,
    device_id: int,
    link_id: int,
    sequence: int,
    message_id: int | None = None,
) -> bytes:
    """Build the native brokered P2P-inner teardown for one exact direct link.

    This is not an AV STOP/CLOSE record.  It mirrors ``giot_eif_send_hungup_msg`` and releases the
    A4-created MTP route after media has stopped, so closing the host UDP socket does not leave a
    camera link slot occupied until timeout.
    """

    if not 0 < link_id <= 0xFFFFFF:
        raise ValueError("route link id must be a non-zero 24-bit value")
    if message_id is None:
        message_id = secrets.randbits(31)
    frame = new_header(
        0xB9,
        0x4C,
        node.session_id,
        sequence,
        randomized_flags(mode=2, proc=3),
    )
    frame[0] = 0x7E
    struct.pack_into("<I", frame, 0x18, 1)
    struct.pack_into("<Q", frame, 0x1C, device_id)
    struct.pack_into("<Q", frame, 0x24, access_id)
    struct.pack_into("<I", frame, 0x2C, message_id & 0x7FFFFFFF)
    # p2p-inner payload: type=0 (hangup), route id, peer route id, native hangup reason.
    struct.pack_into("<I", frame, 0x38, link_id)
    struct.pack_into("<I", frame, 0x3C, link_id)
    struct.pack_into("<I", frame, 0x40, 0x4E22)
    return finish_mode2(frame, node.session_key)


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
