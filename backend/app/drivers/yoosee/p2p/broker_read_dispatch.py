"""Finite single-owner datagram composition for allowlisted reads only.

No sockets, threads, login, send retries or production entry point. The future
account socket owner sends returned wires once and consumes completions directly.
"""

import secrets
import time
from collections.abc import Callable
from dataclasses import dataclass, field

from .broker_maintenance import BrokerMaintenance
from .broker_reads import BrokerReads, ReadCompletion, ReadRequest
from .broker_transactions import ReceiptTicket
from .contracts import CertifiedNode
from .model_protocol import build_model_read


@dataclass(frozen=True, slots=True)
class PreparedRead:
    request: ReadRequest
    wire: bytes = field(repr=False)


@dataclass(frozen=True, slots=True)
class ReadDispatchBatch:
    acknowledgements: tuple[bytes, ...] = field(repr=False)
    completions: tuple[ReadCompletion, ...]


class ReadDispatchError(ValueError):
    def __init__(self, cancelled: tuple[ReadCompletion, ...]):
        super().__init__("broker read dispatcher failed; session must close")
        self.cancelled = cancelled


class BrokerReadDispatcher:
    """One authenticated node generation, <=15 seconds, eight pending reads.

    Resource/fragment limits belong to BrokerMaintenance. Poll on silence; no
    queue, callbacks retained, heartbeat, credential renewal or automatic retry.
    This finite composition must not be promoted to a long-lived session owner.
    """

    def __init__(self, node: CertifiedNode, generation: int, *,
                 clock: Callable[[], float] = time.monotonic):
        self._reads = BrokerReads(node.session_id, generation, node.next_sequence, clock=clock)
        self._maintenance = BrokerMaintenance(node, clock=clock)
        self._node: CertifiedNode | None = node
        self._generation, self._clock = generation, clock
        self._deadline = clock() + 15
        self.closed = False

    @property
    def pending_count(self) -> int:
        return self._reads.pending_count

    def prepare(self, device_id: int, path: str, timeout: float) -> PreparedRead:
        if self.closed or self._clock() >= self._deadline:
            raise ValueError("broker read dispatcher expired or closed")
        if type(timeout) not in (int, float) or timeout > self._deadline - self._clock():
            raise ValueError("read exceeds remaining session lifetime")
        request = self._reads.register(device_id, path, timeout)
        assert self._node is not None
        try:
            wire = build_model_read(self._node, device_id, path, request.ticket.sequence,
                                    secrets.randbits(31))
            return PreparedRead(request, wire)
        except BaseException:
            self._reads.cancel(request.ticket)
            raise

    def cancel(self, ticket: ReceiptTicket) -> ReadCompletion | None:
        return self._reads.cancel(ticket)

    def receive(self, wire: bytes, peer: tuple[str, int], *, generation: int) -> ReadDispatchBatch:
        if self.closed:
            raise ValueError("broker read dispatcher closed")
        if type(generation) is not int or generation != self._generation:
            return ReadDispatchBatch((), ())
        message: bytes | None = None

        def inspect(plain: bytes) -> None:
            nonlocal message
            message = plain

        try:
            acknowledgements = self._maintenance.receive(wire, peer, inspect_message=inspect)
            # Complete only after ACK construction succeeds; otherwise a failed
            # packet would remove a waiter without delivering its terminal result.
            result = self._reads.accept(message, generation=generation) if message is not None else None
            return ReadDispatchBatch(acknowledgements, (result,) if result is not None else ())
        except Exception:
            raise ReadDispatchError(self.close()) from None
        except BaseException:
            self.close()
            raise

    def poll(self) -> tuple[ReadCompletion, ...]:
        expired = self._reads.poll()
        return expired + (self.close() if self._clock() >= self._deadline else ())

    def close(self) -> tuple[ReadCompletion, ...]:
        cancelled = self._reads.close()
        self._maintenance.close()
        self._node = None
        self.closed = True
        return cancelled
