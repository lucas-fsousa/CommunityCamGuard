"""Bounded ACK-only broker servicing during experimental media reception.

No socket, command dispatch, heartbeat, credential refresh or media producer.
The caller must poll fairly with its existing UDP socket and close this owner.
"""

import struct
import time
from collections.abc import Callable

from .access_protocol import build_mode2_response_ack
from .contracts import CertifiedNode
from .resource_service_protocol import (
    FragmentReassembler,
    build_fragment_ack,
    decode_fragment_packet,
)
from .session_io import decrypt_node_frame
from .tcp_budget import TcpProfile, validate_profile


class BrokerMaintenance:
    """One broker/session, default 15 seconds, 512 datagrams, 1 MiB; no history."""

    def __init__(self, node: CertifiedNode, *, clock: Callable[[], float] = time.monotonic,
                 profile: TcpProfile = TcpProfile.SHORT):
        validate_profile(profile)
        self._node: CertifiedNode | None = node
        self._clock, self._deadline = clock, clock() + profile.budget.broker_seconds
        self._fragments = FragmentReassembler(max_groups=2)
        self.closed = False
        self.datagrams = self.received_bytes = self.messages = self.acknowledgements = 0

    def close(self) -> None:
        self.closed = True
        self._node = None
        self._fragments = FragmentReassembler(max_groups=2)

    def receive(self, wire: bytes, peer: tuple[str, int], *,
                inspect_message: Callable[[bytes], None] | None = None) -> tuple[bytes, ...]:
        if self.closed:
            raise ValueError("broker maintenance is closed")
        try:
            self.datagrams += 1
            self.received_bytes += len(wire)
            if (self._clock() >= self._deadline or self.datagrams > 512
                    or self.received_bytes > 1024 * 1024 or len(wire) > 4096):
                raise ValueError("broker maintenance budget exhausted")
            node = self._node
            assert node is not None
            if peer != node.address:
                return ()
            replies = []
            if wire[:2] == b"\x70\x01":
                fragment = decode_fragment_packet(wire)
                # Outer fragment identity is a group identifier, not the broker
                # session. Correlate the decrypted inner frame after assembly.
                assembled = self._fragments.add(fragment)
                replies.append(build_fragment_ack(fragment))
                self.acknowledgements += 1
                if assembled is None:
                    return tuple(replies)
                wire = assembled
            if len(wire) < 32 or (wire[0x16] & 3) != 2:
                return tuple(replies)
            plain = decrypt_node_frame(wire, node)
            if plain is None or struct.unpack_from("<Q", plain, 4)[0] != node.session_id:
                return tuple(replies)
            self.messages += 1
            if inspect_message is not None:
                inspect_message(plain)
            flags = struct.unpack_from("<I", plain, 20)[0]
            if not flags & (1 << 20) and (flags >> 18) & 3:
                replies.append(build_mode2_response_ack(node, plain))
                self.acknowledgements += 1
            return tuple(replies)
        except (ValueError, TypeError):
            self.close()
            raise
