"""Sole-owner pulse clock and reserved OFF; no socket, thread, login or production trigger."""

import math
import secrets
import struct
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field

from .access_protocol import build_mode2_response_ack
from .account_output_budget import AccountOutputBudget
from .broker_sequences import BrokerSequences
from .contracts import CertifiedNode
from .crypto import gute_mode1_xor_checksum
from .siren import build_siren_action, parse_siren_action_response
from .siren_off_probe import SirenOffProbe


@dataclass(frozen=True, slots=True)
class SirenPulseSpec:
    access_id: int = field(repr=False)
    device_id: int = field(repr=False)
    seconds: int

    def __post_init__(self):
        if (type(self.access_id) is not int or not 0 < self.access_id < 1 << 64
                or type(self.device_id) is not int or not 0 < self.device_id < 1 << 64
                or type(self.seconds) is not int or self.seconds not in (2, 5, 10)):
            raise ValueError('typed bounded siren pulse required')


@dataclass(frozen=True, slots=True)
class SirenPacket:
    kind: str
    wire: bytes = field(repr=False)


@dataclass(frozen=True, slots=True)
class SirenPulseResult:
    status: str
    start_attempted: bool
    off_attempts: int
    off_confirmed: bool
    on_error: int | None
    off_error: int | None
    transport_acknowledged: bool
    send_failed: bool


class SirenPulseJob:
    """Cancel before ON is silent; afterward cancel MUST drain OFF on the old socket.

    next_packet consumes an attempt/reserved capacity before I/O. ON is never
    replayed. Only the identical OFF is retried once, within2seconds. Transport
    receipts never prove OFF; AD/error0 AND independent exact B7 state1 are required.
    Unknown OFF poisons the reservation until the entire owner is retired.
    """

    def __init__(self, node: CertifiedNode, sequences: BrokerSequences, output: AccountOutputBudget,
                 spec: SirenPulseSpec, *, generation: int, clock: Callable[[], float] = time.monotonic,
                 admit_until: float | None = None):
        now = clock()
        admission = now+1 if admit_until is None else admit_until
        if (not isinstance(spec, SirenPulseSpec) or not sequences.matches(node.session_id, generation)
                or not math.isfinite(admission) or not 0 < admission-now <= 1):
            raise ValueError('exact native siren account required')
        self._reserve = output.reserve_siren()
        try:
            self._on_seq, self._off_seq, *read_sequences = sequences.reserve(5)
            self._probe = SirenOffProbe(node, spec.device_id, generation, tuple(read_sequences))
            self._on_id = secrets.randbits(31)
            self._off_id = self._on_id ^ 1
            self._on = build_siren_action(node, spec.access_id, spec.device_id, True, self._on_seq, self._on_id)
            self._off = build_siren_action(node, spec.access_id, spec.device_id, False, self._off_seq, self._off_id)
        except BaseException:
            self._reserve.close()
            raise
        self._node, self._source, self._generation = node, sequences, generation
        self._clock, self._seconds = clock, spec.seconds
        self._admit_until = admission
        self._hold_until = self._on_until = self._off_until = self._retry_at = 0.0
        self._started = self._failed = self._on_ack = self._off_ack = False
        self._on_error: int | None = None
        self._off_error: int | None = None
        self._offs = self._receipts = 0
        self._cancel = threading.Event()
        self.result: SirenPulseResult | None = None

    def cancel(self) -> None:
        self._cancel.set()

    def send_failed(self) -> None:
        self._failed = True
        self.cancel()

    def _finish(self, status: str, confirmed: bool = False) -> None:
        self.result = SirenPulseResult(status, self._started, self._offs, confirmed,
            self._on_error, self._off_error, self._on_ack and self._off_ack, self._failed)
        if not self._started or confirmed:
            self._reserve.close()
        self._on = self._off = b''

    def next_packet(self) -> SirenPacket | None:
        if self.result is not None:
            return None
        now = self._clock()
        if not self._started:
            if self._cancel.is_set() or now >= self._admit_until or not self._source.matches(self._node.session_id, self._generation):
                self._finish('cancelled')
                return None
            self._reserve.charge('on', len(self._on))
            self._started = True
            self._hold_until, self._on_until = now+self._seconds, now+1.5
            return SirenPacket('on', self._on)
        stop = (self._cancel.is_set() or self._on_error not in (None, 0)
                or now >= self._hold_until or (self._on_error is None and now >= self._on_until))
        if not self._offs:
            if not stop:
                return None
            self._off_until = now+2
        else:
            if self._off_error not in (None, 0) or self._probe.invalid or self._probe.state == 1:
                confirmed = self._off_error == 0 and self._probe.state == 1 and not self._probe.invalid
                self._finish('completed' if confirmed and self._on_error == 0 and not self._cancel.is_set()
                             else 'cancelled' if confirmed else 'off_rejected', confirmed)
                return None
            if now >= self._off_until:
                self._finish('off_unconfirmed')
                return None
        if self._off_error == 0:
            if self._probe.state == 2 and self._offs < 2 and now >= self._retry_at:
                self._reserve.charge('off', len(self._off))
                self._offs += 1
                self._retry_at = now+0.5
                return SirenPacket('off', self._off)
            probe = self._probe.next_packet(now)
            if probe is not None:
                self._reserve.charge('read', len(probe))
                return SirenPacket('read', probe)
            return None
        if self._offs < 2 and now >= self._retry_at:
            self._reserve.charge('off', len(self._off))
            self._offs += 1
            self._retry_at = now+0.5
            return SirenPacket('off', self._off)
        return None

    def observe(self, plain: bytes, peer: tuple[str, int], *, generation: int) -> tuple[SirenPacket, ...]:
        if (self.result is not None or not self._started or peer != self._node.address
                or type(generation) is not int or generation != self._generation
                or not isinstance(plain, bytes) or not 24 <= len(plain) <= 4096
                or plain[0] not in (0x7E, 0x7F) or struct.unpack_from('<H', plain, 2)[0] != len(plain)):
            return ()
        flags = struct.unpack_from('<I', plain, 20)[0]
        if struct.unpack_from('<Q', plain, 4)[0] != self._node.session_id or (flags >> 16) & 3 != 2:
            return ()
        if self._offs and self._clock() >= self._off_until:
            return ()
        if not self._offs and self._clock() >= self._on_until:
            return ()
        if self._offs and self._off_error == 0 and self._probe.observe(plain, generation=generation):
            return self._receipt(plain, flags)
        if flags & (1 << 20):
            if (len(plain) != 32 or plain[1] != 0xAC or flags & (1 << 21)
                    or struct.unpack_from('<I', plain, 16)[0] != gute_mode1_xor_checksum(plain)):
                return ()
            seq = struct.unpack_from('<I', plain, 12)[0]
            accepted = struct.unpack_from('<H', plain, 26)[0] == 0
            if seq == self._on_seq and not self._offs:
                self._on_ack |= accepted
                if not accepted: self.cancel()
            elif seq == self._off_seq and self._offs:
                self._off_ack |= accepted
            return ()
        message = self._off_id if self._offs else self._on_id
        code = parse_siren_action_response(plain, message)
        if code is None:
            return ()
        if self._offs:
            if self._off_error in (None, 0): self._off_error = code
        else:
            if self._on_error in (None, 0): self._on_error = code
        return self._receipt(plain, flags)

    def _receipt(self, plain: bytes, flags: int) -> tuple[SirenPacket, ...]:
        if (flags >> 18) & 3 and self._receipts < 6:
            reply = build_mode2_response_ack(self._node, plain)
            self._reserve.charge('receipt', len(reply))
            self._receipts += 1
            return (SirenPacket('receipt', reply),)
        return ()
