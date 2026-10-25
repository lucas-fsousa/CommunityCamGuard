"""One type-12 lamp-state read on an existing broker owner; no write, socket or retry."""

import math
import secrets
import struct
import time
from collections.abc import Callable
from dataclasses import dataclass, field

from .access_protocol import build_mode2_response_ack
from .broker_sequences import BrokerSequences
from .broker_transactions import BrokerTransactions
from .contracts import CertifiedNode
from .white_light import (
    build_white_light_receipt,
    build_white_light_request,
    extract_white_light_state,
    parse_white_light_response,
)


@dataclass(frozen=True, slots=True)
class LampReadSpec:
    access_id: int = field(repr=False)
    device_id: int = field(repr=False)


@dataclass(frozen=True, slots=True)
class LampReadResult:
    status: str
    enabled: bool | None = None
    transport_acknowledged: bool = False


class LampReadJob:
    def __init__(self, node: CertifiedNode, sequences: BrokerSequences, spec: LampReadSpec, *,
                 generation: int, deadline: float, clock: Callable[[], float] = time.monotonic):
        now = clock()
        if (not isinstance(spec, LampReadSpec) or type(spec.access_id) is not int
                or not 0 <= spec.access_id < 1 << 64 or type(spec.device_id) is not int
                or not 0 < spec.device_id < 1 << 64 or type(deadline) not in (int, float)
                or not math.isfinite(deadline) or not 0 < deadline-now <= 2):
            raise ValueError("bounded typed lamp-state read required")
        self._receipts = BrokerTransactions(node.session_id, generation, node.next_sequence,
                                            sequences=sequences, clock=clock)
        try:
            ticket = self._receipts.register(0xB9, deadline-now)
            self._receipt_sequence = sequences.allocate()
            # The account-wide sequence is never recycled in this generation.
            self._request_id = ticket.sequence
            self._wire = build_white_light_request(node, spec.access_id, spec.device_id,
                None, ticket.sequence, secrets.randbits(31), self._request_id)
        except BaseException:
            self._receipts.close()
            raise
        self._node: CertifiedNode | None = node
        self._spec, self._generation, self._clock = spec, generation, clock
        self._deadline = deadline
        self._sent = self._ack = False
        self.result: LampReadResult | None = None

    def _finish(self, status: str, enabled: bool | None = None) -> None:
        self.result = LampReadResult(status, enabled, self._ack)
        self._receipts.close()  # does NOT retire the shared sequence namespace
        self._wire = b""
        self._node = None

    def cancel(self) -> None:
        if self.result is None:
            self._finish("cancelled")

    def next_packet(self) -> bytes | None:
        if self.result is not None:
            return None
        if not self._receipts.active:
            self.cancel()
        elif self._clock() >= self._deadline:
            self._finish("timeout")
        elif not self._sent:
            self._sent = True
            return self._wire
        return None

    def observe(self, plain: bytes, peer: tuple[str, int], *, generation: int) -> tuple[bytes, ...]:
        node = self._node
        if (node is None or not self._sent or generation != self._generation
                or peer != node.address or self._clock() >= self._deadline
                or not self._receipts.active):
            return ()
        receipt = self._receipts.accept(plain, generation=generation)
        if receipt is not None:
            self._ack = receipt.accepted
            if not receipt.accepted:
                self._finish("transport_rejected")
            return ()  # A positive transport ACK is not a lamp state.
        if not 60 <= len(plain) <= 4096:
            return ()
        flags = struct.unpack_from("<I", plain, 20)[0]
        if (struct.unpack_from("<Q", plain, 4)[0] != node.session_id
                or (flags >> 16) & 3 != 2 or flags & (1 << 20)
                or struct.unpack_from("<Q", plain, 28)[0] != self._spec.access_id
                or struct.unpack_from("<Q", plain, 36)[0] != self._spec.device_id):
            return ()
        try:
            parsed = parse_white_light_response(plain, 12)
        except RecursionError:
            return ()
        if parsed is None or parsed[0] != self._request_id:
            return ()
        packets = []
        if (flags >> 18) & 3:
            packets.append(build_mode2_response_ack(node, plain))
        packets.append(build_white_light_receipt(node, plain, self._receipt_sequence))
        enabled = extract_white_light_state(parsed[1])
        self._finish("response" if enabled is not None else "invalid_response", enabled)
        return tuple(packets)
