"""Shared bounded B9 read exchange for Yoosee onboard-playback operations."""

from __future__ import annotations

import socket
import struct
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Generic, TypeVar

from .contracts import CertifiedNode, P2PProbeError
from .onboard_playback_carrier import build_onboard_playback_receipt
from .session_io import acknowledge_reliable_node_frame, decrypt_node_frame, receive_datagrams

ResponseT = TypeVar("ResponseT")

# BuiltIn playback commands are recovered from the SDK, but sending command 16 through the bare
# brokered-control route caused visible LED activity on camera 3 instead of returning a list. Keep
# all public live entrypoints fail-closed until the SDK's prerequisite connection state is cloned.
_RUNTIME_PLAYBACK_READ_CERTIFIED = False


def require_runtime_playback_read_certified() -> None:
    """Reject live SD-card queries while their transport semantics are not physically safe."""

    if not _RUNTIME_PLAYBACK_READ_CERTIFIED:
        raise P2PProbeError("Yoosee onboard playback transport is not runtime-certified")


@dataclass(frozen=True, slots=True)
class BuiltInReadExchange(Generic[ResponseT]):
    transport_acknowledged: bool
    application_acknowledged: bool
    responses: tuple[ResponseT, ...]


def exchange_built_in_read(
    sock: socket.socket,
    node: CertifiedNode,
    request: bytes,
    *,
    message_id: int,
    sequence: int,
    timeout: float,
    parse_response: Callable[[bytes], ResponseT | None],
    response_set_complete: Callable[[tuple[ResponseT, ...]], bool],
    retries: int = 3,
    deadline: float | None = None,
) -> BuiltInReadExchange[ResponseT]:
    """Deliver one idempotent read and collect only correlated parsed responses."""

    if retries < 1:
        raise ValueError("onboard playback retries must be positive")
    transport_acknowledged = False
    application_acknowledged = False
    responses: list[ResponseT] = []
    for retry in range(retries):
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
                if plain[1] == 0xB9:
                    transport_acknowledged = True
                elif plain[1] == 0xBA:
                    application_acknowledged = True
                continue
            if plain[1] == 0xBA and len(plain) >= 0x34:
                if struct.unpack_from("<I", plain, 0x2C)[0] == message_id:
                    application_acknowledged = True
                    acknowledge_reliable_node_frame(sock, node, plain)
                continue
            parsed = parse_response(plain)
            if parsed is None:
                continue
            acknowledge_reliable_node_frame(sock, node, plain)
            sock.sendto(
                build_onboard_playback_receipt(
                    node,
                    plain,
                    (sequence + retry + 1) & 0xFFFFFFFF,
                ),
                node.address,
            )
            responses.append(parsed)
            if response_set_complete(tuple(responses)):
                return BuiltInReadExchange(
                    transport_acknowledged,
                    application_acknowledged,
                    tuple(responses),
                )
    return BuiltInReadExchange(
        transport_acknowledged,
        application_acknowledged,
        tuple(responses),
    )
