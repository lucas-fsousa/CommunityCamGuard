"""Socket-free, single-owner correlation of broker transport receipts.

Not application-command success, credential renewal, retries or a shared reader.
Only complete decrypted mode-2 ACKs enter here, after broker peer verification
and bounded fragment assembly by the account owner. No payload/key is retained.
"""

import math
import struct
import time
from collections.abc import Callable
from dataclasses import dataclass

from .broker_sequences import BrokerSequences
from .crypto import gute_mode1_xor_checksum

MAX_PENDING = 8
MAX_TIMEOUT = 10.0


@dataclass(frozen=True, slots=True)
class ReceiptTicket:
    generation: int
    sequence: int
    kind: int


@dataclass(frozen=True, slots=True)
class TransportReceipt:
    ticket: ReceiptTicket
    result: int

    @property
    def accepted(self) -> bool:
        return self.result == 0


class BrokerTransactions:
    """At most eight pending ACKs, ten seconds each, no sequence reuse.

    Caller owns serialization, authenticated generation and peer checks. Poll
    expired tickets explicitly so their waiters receive timeout notifications;
    register never silently evicts one. Sequence exhaustion requires a new
    generation, not wraparound matching an old datagram. This is not thread-safe.
    """

    def __init__(self, session_id: int, generation: int, next_sequence: int, *,
                 clock: Callable[[], float] = time.monotonic,
                 sequences: BrokerSequences | None = None):
        seed = BrokerSequences(session_id, generation, next_sequence)
        if sequences is not None and not sequences.matches(session_id, generation):
            raise ValueError("broker sequence identity mismatch")
        self._sequences = sequences if sequences is not None else seed
        self._owns_sequences = sequences is None
        self._session, self._generation = session_id, generation
        self._clock = clock
        self._pending: dict[int, tuple[ReceiptTicket, float]] = {}
        self.closed = False

    @property
    def pending_count(self) -> int:
        return len(self._pending)

    @property
    def active(self) -> bool:
        return not self.closed and self._sequences.matches(self._session, self._generation)

    def register(self, kind: int, timeout: float) -> ReceiptTicket:
        if not self.active:
            raise ValueError("broker transactions closed")
        if (type(kind) is not int or not 0 <= kind <= 255
                or type(timeout) not in (int, float) or not math.isfinite(timeout)
                or not 0 < timeout <= MAX_TIMEOUT):
            raise ValueError("invalid broker transaction bounds")
        if len(self._pending) >= MAX_PENDING:
            raise ValueError("broker transaction capacity exhausted")
        ticket = ReceiptTicket(self._generation, self._sequences.allocate(), kind)
        self._pending[ticket.sequence] = ticket, self._clock() + timeout
        return ticket

    def poll(self) -> tuple[ReceiptTicket, ...]:
        now = self._clock()
        expired = tuple(ticket for ticket, deadline in self._pending.values() if now >= deadline)
        for ticket in expired:
            del self._pending[ticket.sequence]
        return expired

    def cancel(self, ticket: ReceiptTicket) -> bool:
        """Withdraw a failed/cancelled send, never recycle its sequence."""
        pending = self._pending.get(ticket.sequence)
        if pending is None or pending[0] != ticket:
            return False
        del self._pending[ticket.sequence]
        return True

    def accept(self, plain: bytes, *, generation: int) -> TransportReceipt | None:
        if (not self.active or type(generation) is not int or generation != self._generation
                or not isinstance(plain, bytes) or len(plain) != 32
                or plain[0] not in (0x7E, 0x7F)
                or struct.unpack_from("<H", plain, 2)[0] != 32
                or struct.unpack_from("<Q", plain, 4)[0] != self._session
                or struct.unpack_from("<I", plain, 16)[0] != gute_mode1_xor_checksum(plain)):
            return None
        flags = struct.unpack_from("<I", plain, 20)[0]
        if ((flags >> 16) & 3) != 2 or not flags & (1 << 20) or flags & (1 << 21):
            return None
        sequence = struct.unpack_from("<I", plain, 12)[0]
        pending = self._pending.get(sequence)
        if pending is None:
            return None
        ticket, deadline = pending
        if plain[1] != ticket.kind or self._clock() >= deadline:
            return None
        del self._pending[sequence]
        return TransportReceipt(ticket, struct.unpack_from("<H", plain, 26)[0])

    def close(self) -> tuple[ReceiptTicket, ...]:
        cancelled = tuple(ticket for ticket, _ in self._pending.values())
        self._pending.clear()
        self._session = 0
        self.closed = True
        if self._owns_sequences:
            self._sequences.close()
        return cancelled
