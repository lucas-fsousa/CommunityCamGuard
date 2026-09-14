"""Prepared native PTZ UDP route; no discovery, login, reconnect or fallback.

The caller must reserve the camera and validate exact identity/axis/profile BEFORE
constructing this adapter. Not registered as a production driver transport yet.
"""
from __future__ import annotations

import secrets
import select
import socket
import time
from typing import TypedDict

from .contracts import CertifiedNode
from .ptz_protocol import build_ptz_receipt, build_ptz_request, parse_ptz_reply
from .session_io import acknowledge_reliable_node_frame, decrypt_node_frame


class _ReplyIdentity(TypedDict):
    node: CertifiedNode
    access_id: int
    device_id: int
    sequence: int
    message_id: int
    request_id: int


class NativePtzRoute:
    def __init__(self, sock: socket.socket, node: CertifiedNode, *, access_id: int,
                 device_id: int, direction: str, sequence: int,
                 allowed_directions: frozenset[str] | None = None) -> None:
        self._sock = sock
        self._node = node
        self._access_id = access_id
        self._device_id = device_id
        self._direction = direction
        self._allowed_directions = allowed_directions or frozenset({direction})
        if direction not in self._allowed_directions:
            raise ValueError("PTZ direction was not verified for this route")
        self._started = False
        self._closed = False
        self._released = False
        self._transport = False
        self._peer = False
        self._application = False
        self._error = False
        self._confirmed = False
        self._start = build_ptz_request(node, access_id, device_id, direction, pressed=True, sequence=sequence,
                                        message_id=secrets.randbits(31), request_id=secrets.randbits(32))
        self._release_ids: _ReplyIdentity = dict(node=node, access_id=access_id, device_id=device_id,
                                sequence=(sequence + 1) & 0xFFFFFFFF,
                                message_id=secrets.randbits(31), request_id=secrets.randbits(32))
        self._release = build_ptz_request(**self._release_ids, direction=direction, pressed=False)
        self._receipt_sequence = (sequence + 2) & 0xFFFFFFFF
        sock.setblocking(False)

    def send_start(self) -> None:
        if self._closed or self._started or self._released:
            raise RuntimeError("native PTZ START cannot be repeated or follow RELEASE")
        self._started = True  # ambiguous send failures must not permit another START
        self._sock.sendto(self._start, self._node.address)

    def send_release(self) -> None:
        if self._closed:
            raise RuntimeError("native PTZ route is closed")
        self._released = True
        # Same reliable message/sequence on retries: no new motion or route allocation.
        self._sock.sendto(self._release, self._node.address)

    def confirm_release(self, *, deadline: float) -> bool:
        if self._closed or not self._released:
            return False
        # Deadline AND packet-count bounds: unrelated traffic cannot create an unbounded loop.
        for _ in range(64):
            # Once delivery is confirmed, drain immediately arriving contradictory
            # replies, but do not stall every gesture for another half second.
            remaining = min(0.02 if self._confirmed else 0.5, deadline - time.monotonic())
            if remaining <= 0:
                break
            readable, _, _ = select.select([self._sock], [], [], remaining)
            if not readable:
                break
            try:
                wire, peer = self._sock.recvfrom(4097)
            except BlockingIOError:
                continue
            if peer != self._node.address or len(wire) > 4096:
                continue
            plain = decrypt_node_frame(wire, self._node)
            if plain is None:
                continue
            response = parse_ptz_reply(plain, **self._release_ids)
            if response is None:
                continue
            self._transport |= response.transport_receipt
            self._peer |= response.peer_receipt
            if response.error_code is not None:
                self._error |= response.error_code != 0
                self._application |= response.error_code == 0
            self._confirmed = not self._error and (self._application or (self._transport and self._peer))
            acknowledge_reliable_node_frame(self._sock, self._node, plain)
            if response.error_code is not None:
                # Allocate before send: an ambiguous failure must not reuse an ID.
                sequence = self._receipt_sequence
                self._receipt_sequence = (sequence + 1) & 0xFFFFFFFF
                self._sock.sendto(build_ptz_receipt(plain, self._node, sequence), self._node.address)
        # A later explicit error in this observation window beats earlier receipts/success.
        return not self._error and (self._application or (self._transport and self._peer))

    def renew(self, direction: str | None = None) -> NativePtzRoute:
        """Transfer one clean stopped socket to fresh request IDs and sequences."""
        if self._closed or not self._released or not self._confirmed or self._error:
            raise RuntimeError("only a confirmed stopped PTZ route can be renewed")
        direction = self._direction if direction is None else direction
        if direction not in self._allowed_directions:
            raise ValueError("PTZ direction was not verified for this route")
        route = NativePtzRoute(self._sock, self._node, access_id=self._access_id,
                               device_id=self._device_id, direction=direction,
                               sequence=self._receipt_sequence,
                               allowed_directions=self._allowed_directions)
        self._closed = True  # socket ownership transferred, not duplicated
        return route

    def close(self) -> None:
        if not self._closed:
            self._closed = True
            self._sock.close()
