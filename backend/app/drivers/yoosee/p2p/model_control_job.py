"""Fixed typed scalar controls on an existing owner; no raw path, socket or replay."""

import math
import secrets
import struct
import time
from collections.abc import Callable
from dataclasses import dataclass

from .broker_sequences import BrokerSequences
from .broker_transactions import BrokerTransactions
from .contracts import CertifiedNode
from .model_write_protocol import parse_model_write_response
from .night_vision import NIGHT_VISION_VALUES, build_night_vision_write
from .orientation import ORIENTATION_VALUES, build_orientation_write
from .volume import VOLUME_RAW_BY_PERCENT, build_volume_write


@dataclass(frozen=True, slots=True)
class VolumeWriteSpec:
    device_id: int
    percent: int

    def __post_init__(self):
        if (type(self.device_id) is not int or not 0 < self.device_id < 1 << 64
                or type(self.percent) is not int or self.percent not in VOLUME_RAW_BY_PERCENT):
            raise ValueError('typed camera volume required')


@dataclass(frozen=True, slots=True)
class OrientationWriteSpec:
    device_id: int
    orientation: str

    def __post_init__(self):
        if (type(self.device_id) is not int or not 0 < self.device_id < 1 << 64
                or type(self.orientation) is not str or self.orientation not in ORIENTATION_VALUES):
            raise ValueError('typed camera orientation required')


@dataclass(frozen=True, slots=True)
class NightVisionWriteSpec:
    device_id: int
    mode: str

    def __post_init__(self):
        if (type(self.device_id) is not int or not 0 < self.device_id < 1 << 64
                or type(self.mode) is not str or self.mode not in NIGHT_VISION_VALUES):
            raise ValueError('typed camera night-vision mode required')


ControlWriteSpec = VolumeWriteSpec | OrientationWriteSpec | NightVisionWriteSpec


@dataclass(frozen=True, slots=True)
class ControlWriteResult:
    status: str
    error_code: int | None = None
    transport_acknowledged: bool = False


class ControlWriteJob:
    def __init__(self, node: CertifiedNode, sequences: BrokerSequences, spec: ControlWriteSpec, *,
                 generation: int, deadline: float, clock: Callable[[], float] = time.monotonic):
        if not isinstance(spec, (VolumeWriteSpec, OrientationWriteSpec, NightVisionWriteSpec)) or not math.isfinite(deadline) or not 0 < deadline-clock() <= 2:
            raise ValueError('bounded typed control write required')
        self._receipts = BrokerTransactions(node.session_id, generation, node.next_sequence,
                                            sequences=sequences, clock=clock)
        self._message_id = secrets.randbits(31)
        try:
            ticket = self._receipts.register(0xD2, deadline-clock())
            if isinstance(spec, VolumeWriteSpec):
                self._wire = build_volume_write(node, spec.device_id, spec.percent, ticket.sequence, self._message_id)
            elif isinstance(spec, OrientationWriteSpec):
                self._wire = build_orientation_write(node, spec.device_id, spec.orientation, ticket.sequence, self._message_id)
            else:
                self._wire = build_night_vision_write(node, spec.device_id, spec.mode, ticket.sequence, self._message_id)
        except BaseException:
            self._receipts.close()
            raise
        self._node: CertifiedNode | None = node
        self._generation, self._deadline, self._clock = generation, deadline, clock
        self._sent = self._ack = False
        self.result: ControlWriteResult | None = None

    def _finish(self, status: str, error: int | None = None) -> None:
        self.result = ControlWriteResult(status, error, self._ack)
        self._receipts.close()  # Never close the account-wide sequence allocator.
        self._node = None
        self._wire = b''

    def cancel(self) -> None:
        if self.result is None:
            self._finish('cancelled')  # A sent scalar has unknown outcome; never replay it.

    def next_packet(self) -> bytes | None:
        if self.result is None:
            if not self._receipts.active:
                self.cancel()
            elif self._clock() >= self._deadline:
                self._finish('timeout')
            elif not self._sent:
                self._sent = True
                return self._wire
        return None

    def observe(self, plain: bytes, peer: tuple[str, int], *, generation: int) -> None:
        node = self._node
        if (node is None or not self._sent or peer != node.address or generation != self._generation
                or self._clock() >= self._deadline or not self._receipts.active):
            return
        receipt = self._receipts.accept(plain, generation=generation)
        if receipt is not None:
            self._ack = receipt.accepted
            if not receipt.accepted:
                self._finish('transport_rejected')
            return
        if not 54 <= len(plain) <= 4096:
            return
        flags = struct.unpack_from('<I', plain, 20)[0]
        if struct.unpack_from('<Q', plain, 4)[0] != node.session_id or (flags >> 16) & 3 != 2 or flags & (1 << 20):
            return
        code = parse_model_write_response(plain, self._message_id)
        if code is not None:
            self._finish('response', code)
