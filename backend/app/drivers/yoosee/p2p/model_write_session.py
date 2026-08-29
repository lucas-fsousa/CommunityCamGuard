"""Bounded UDP exchange for an internally selected scalar thing-model write."""

from __future__ import annotations

import secrets
import socket
import struct
import time

from .contracts import CertifiedNode, ModelWriteResult, OnlineDevice
from .model_write_protocol import ScalarModelValue, build_model_write, parse_model_write_response
from .session_io import acknowledge_reliable_node_frame, decrypt_node_frame, receive_datagrams


def exchange_model_write(
    sock: socket.socket,
    node: CertifiedNode,
    device: OnlineDevice,
    path: str,
    value: ScalarModelValue,
    sequence: int,
    timeout: float,
    *,
    retries: int = 3,
    deadline: float | None = None,
) -> ModelWriteResult:
    """Send a scalar D2 selected by a feature module and await its correlated D3."""

    if retries < 1:
        raise ValueError("model-write retries must be positive")
    message_id = secrets.randbits(31)
    request = build_model_write(node, device.device_id, path, value, sequence, message_id)
    transport_acknowledged = False
    error_code = None
    for _attempt in range(retries):
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
                if plain[1] == 0xD2:
                    transport_acknowledged = True
                continue
            candidate = parse_model_write_response(plain, message_id)
            acknowledge_reliable_node_frame(sock, node, plain)
            if candidate is not None:
                error_code = candidate
                break
        if error_code is not None:
            break
    return ModelWriteResult(transport_acknowledged, error_code)
