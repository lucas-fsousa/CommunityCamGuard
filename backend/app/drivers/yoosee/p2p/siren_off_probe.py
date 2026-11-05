"""Three fixed pre-reserved B7 OFF checks; no allocator, I/O, thread or action."""

import secrets

from ..siren_evidence import siren_state
from .broker_model_reply import match_model_reply
from .broker_transactions import ReceiptTicket
from .contracts import CertifiedNode
from .model_protocol import build_model_read
from .siren import SIREN_READ_PATH


class SirenOffProbe:
    def __init__(self, node: CertifiedNode, device_id: int, generation: int, sequences: tuple[int, ...]):
        if len(sequences) != 3:
            raise ValueError('three reserved siren-state requests required')
        self._node, self._device, self._generation = node, device_id, generation
        self._tickets = tuple(ReceiptTicket(generation, sequence, 0xB7) for sequence in sequences)
        self._wires = tuple(build_model_read(node, device_id, SIREN_READ_PATH, sequence, secrets.randbits(31))
                            for sequence in sequences)
        self._index = 0
        self._next_at = 0.0
        self.state: int | None = None
        self.invalid = False

    def next_packet(self, now: float) -> bytes | None:
        if self.state == 1 or self.invalid or self._index >= 3 or now < self._next_at:
            return None
        wire = self._wires[self._index]
        self._index += 1
        self._next_at = now+0.4
        return wire

    def observe(self, plain: bytes, *, generation: int) -> bool:
        if not self._index:
            return False
        reply = match_model_reply(plain, self._tickets[self._index-1], generation=generation,
                                  session_id=self._node.session_id, device_id=self._device)
        if reply is None:
            return False
        state = siren_state(reply.value) if reply.error_code == 0 else None
        if state is None:
            self.invalid = True
        elif not self.invalid:
            self.state = state
        return True
