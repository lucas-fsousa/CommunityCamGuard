"""Finite typed PTZ scheduler, driven by a future single account socket owner."""

import math
import struct
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field

from .access_protocol import build_mode2_response_ack
from .account_output_budget import AccountOutputBudget
from .broker_sequences import BrokerSequences
from .contracts import CertifiedNode
from .ptz_command_reservation import reserve_ptz_commands
from .ptz_protocol import build_ptz_receipt, parse_ptz_reply
from .ptz_release_delivery import ReleaseDelivery


@dataclass(frozen=True, slots=True)
class PtzSpec:
    """Trusted driver-reviewed gesture, not a public capability assertion."""
    access_id: int = field(repr=False)
    device_id: int = field(repr=False)
    direction: str
    reviewed_directions: frozenset[str]
    duration: float = 0.2


@dataclass(frozen=True, slots=True)
class PtzPacket:
    kind: str
    wire: bytes = field(repr=False)


@dataclass(frozen=True, slots=True)
class ScheduledPtzResult:
    reason: str
    start_attempted: bool
    release_attempts: int
    release_delivery_confirmed: bool
    send_failed: bool


class PtzScheduler:
    """No sockets/threads/sleeps. Owner polls next_packet and feeds decoded frames.

    Caller validates exact camera/profile and owns the pinned old socket. Packet
    return consumes the attempt BEFORE transport I/O; discarding it is ambiguous,
    never permission to retry START. cancel() alone is safe from another thread.
    Keep polling through cleanup on shutdown; never simply discard an active job.
    """

    def __init__(self, node: CertifiedNode, sequences: BrokerSequences, output: AccountOutputBudget, *,
                 generation: int, access_id: int, device_id: int, direction: str,
                 reviewed_directions: frozenset[str], duration: float = 0.2,
                 clock: Callable[[], float] = time.monotonic):
        if type(duration) not in (int, float) or not math.isfinite(duration) or not 0.1 <= duration <= 0.5:
            raise ValueError("PTZ duration must be 0.1..0.5 seconds")
        self._reserve = output.reserve_ptz()
        try:
            self._commands = reserve_ptz_commands(node, sequences, generation=generation,
                access_id=access_id, device_id=device_id, direction=direction,
                reviewed_directions=reviewed_directions)
        except BaseException:
            self._reserve.close()
            raise
        self._node, self._generation = node, generation
        self._access, self._device = access_id, device_id
        self._duration, self._clock = duration, clock
        self._cancel = threading.Event()
        self._admit_until = clock() + 1.0
        self._move_until = self._release_until = self._retry_at = 0.0
        self._settle_until: float | None = None
        self._started = self._failed = self._receipt_prepared = False
        self._releases = 0
        self._delivery: ReleaseDelivery | None = None
        self.result: ScheduledPtzResult | None = None

    def cancel(self) -> None:
        self._cancel.set()

    def send_failed(self) -> None:
        """Owner reports failed/partial send; START is never re-issued."""
        self._failed = True
        self.cancel()

    def _finish(self, reason: str, confirmed: bool = False) -> None:
        self.result = ScheduledPtzResult(reason, self._started, self._releases, confirmed, self._failed)
        if confirmed or not self._started:
            self._reserve.close()
        # Unknown STOP keeps the account's PTZ reservation poisoned: the owner
        # must retire that account, not admit another gesture on the same budget.

    def next_packet(self) -> PtzPacket | None:
        if self.result is not None:
            return None
        now = self._clock()
        if not self._started:
            if self._cancel.is_set() or now >= self._admit_until:
                self._commands.cancel_start()
                self._finish("cancelled")
                return None
            try:
                wire = self._commands.take_start()
            except ValueError:
                self._finish("retired")
                return None
            self._reserve.charge("start", len(wire))
            self._started = True
            self._move_until = now + self._duration
            return PtzPacket("start", wire)
        if self._delivery is None:
            if not self._cancel.is_set() and now < self._move_until:
                return None
            self._release_until = now + 2.0
            self._delivery = ReleaseDelivery(self._node, generation=self._generation,
                access_id=self._access, device_id=self._device,
                correlation=self._commands.release_correlation,
                deadline=self._release_until, clock=self._clock)
        if self._delivery.confirmed and self._settle_until is not None and now >= self._settle_until:
            self._finish("released", True)
            return None
        if now >= self._release_until:
            self._finish("release_unconfirmed")
            return None
        if self._delivery.confirmed or self._releases >= 3 or now < self._retry_at:
            return None
        wire = self._commands.take_release()
        self._reserve.charge("release", len(wire))
        self._releases += 1
        self._retry_at = now + 0.5
        return PtzPacket("release", wire)

    def owns_release_reply(self, plain: bytes) -> bool:
        """Sole-reader routing only: prevents a second generic message ACK path."""
        if not self._releases:
            return False
        ids = self._commands.release_correlation
        return parse_ptz_reply(plain, node=self._node, access_id=self._access,
            device_id=self._device, sequence=ids.sequence, message_id=ids.message_id,
            request_id=ids.request_id) is not None

    def observe(self, plain: bytes, peer: tuple[str, int], *, generation: int) -> tuple[PtzPacket, ...]:
        if self.result is not None or self._delivery is None or not self._releases:
            return ()
        response = self._delivery.observe(plain, peer, generation=generation)
        if self._delivery.confirmed:
            if self._settle_until is None:
                self._settle_until = self._clock() + 0.02
        else:
            self._settle_until = None
        if response is None or response.error_code is None or self._receipt_prepared:
            return ()
        # Two reserved sends cover one correlated application response. Duplicates
        # still update the tracker/error precedence, but never create unbounded ACKs.
        self._receipt_prepared = True
        wires = []
        if (struct.unpack_from("<I", plain, 20)[0] >> 18) & 3:
            wires.append(build_mode2_response_ack(self._node, plain))
        wires.append(build_ptz_receipt(plain, self._node, self._commands.receipt_sequence))
        packets = []
        for wire in wires:
            self._reserve.charge("receipt", len(wire))
            packets.append(PtzPacket("receipt", wire))
        return tuple(packets)
