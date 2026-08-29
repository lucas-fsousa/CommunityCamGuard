"""Bounded session exchange for allowlisted Yoosee thing-model reads."""

from __future__ import annotations

import secrets
import socket
import struct
import time

from .contracts import CertifiedNode, ModelReadResult, OnlineDevice
from .model_protocol import build_model_read, parse_model_read_response, parse_model_report
from .session_io import (
    acknowledge_reliable_node_frame,
    decrypt_node_frame,
    receive_datagrams,
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
