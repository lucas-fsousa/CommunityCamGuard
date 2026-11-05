"""One fixed read-only resource query on the existing account; no socket or retry."""

import math
import struct
import time
from collections.abc import Callable
from dataclasses import dataclass, field

from .alarm_voice import AlarmVoiceCatalog, build_alarm_voice_query, decode_alarm_voice_catalog
from .broker_sequences import BrokerSequences
from .broker_transactions import BrokerTransactions
from .contracts import CertifiedNode
from .quicklz import decompress_level2
from .resource_service_protocol import (
    build_alarm_voice_catalog_request,
    parse_alarm_voice_catalog_response,
)
from .resource_service_session import _expand_compressed_frame


@dataclass(frozen=True, slots=True)
class CatalogReadSpec:
    access_id: int = field(repr=False)
    system: bool

    def __post_init__(self):
        if type(self.access_id) is not int or not 0 < self.access_id < 1 << 64 or type(self.system) is not bool:
            raise ValueError('typed resource catalogue read required')


@dataclass(frozen=True, slots=True)
class CatalogReadResult:
    status: str
    catalog: AlarmVoiceCatalog | None = field(default=None, repr=False)
    transport_acknowledged: bool = False


class CatalogReadJob:
    """Receives bounded decrypted/reassembled C1 frames from the sole reader.

    The worker retains ordinary message/fragment ACK ownership. A matching C0
    transport receipt never substitutes for a successful, complete C1 catalogue.
    """

    def __init__(self, node: CertifiedNode, sequences: BrokerSequences, spec: CatalogReadSpec, *,
                 generation: int, deadline: float, clock: Callable[[], float] = time.monotonic):
        if not isinstance(spec, CatalogReadSpec) or not math.isfinite(deadline) or not 0 < deadline-clock() <= 2:
            raise ValueError('bounded typed catalogue read required')
        self._receipts = BrokerTransactions(node.session_id, generation, node.next_sequence,
                                            sequences=sequences, clock=clock)
        try:
            ticket = self._receipts.register(0xC0, deadline-clock())
            self._sequence = ticket.sequence
            query = build_alarm_voice_query(system=spec.system, access_id=spec.access_id)
            self._wire = build_alarm_voice_catalog_request(node, query, ticket.sequence)
        except BaseException:
            self._receipts.close()
            raise
        self._node: CertifiedNode | None = node
        self._generation, self._deadline, self._clock = generation, deadline, clock
        self._system = spec.system
        self._sent = self._ack = False
        self.result: CatalogReadResult | None = None

    def _finish(self, status: str, catalog: AlarmVoiceCatalog | None = None) -> None:
        self.result = CatalogReadResult(status, catalog, self._ack)
        self._receipts.close()
        self._node = None
        self._wire = b''

    def cancel(self) -> None:
        if self.result is None:
            self._finish('cancelled')

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
                or self._clock() >= self._deadline or not self._receipts.active or not 24 <= len(plain) <= 65535):
            return
        flags = struct.unpack_from('<I', plain, 20)[0]
        if struct.unpack_from('<Q', plain, 4)[0] != node.session_id or (flags >> 16) & 3 != 2:
            return
        receipt = self._receipts.accept(plain, generation=generation)
        if receipt is not None:
            self._ack = receipt.accepted
            if not receipt.accepted:
                self._finish('transport_rejected')
            return
        if (flags & (1 << 20) or plain[1] != 0xC1
                or struct.unpack_from('<I', plain, 16)[0] != self._sequence):
            return  # Reject stale/wrong replies before expanding compressed bytes.
        try:
            expanded, needs_decoder = _expand_compressed_frame(plain, decompress_level2)
            parsed = parse_alarm_voice_catalog_response(expanded) if expanded is not None else None
            if needs_decoder or parsed is None or parsed[1] != 0:
                self._finish('invalid_response')
                return
            catalog = decode_alarm_voice_catalog(parsed[2])
            if (catalog is None or catalog.code != 0 or catalog.reported_total != len(catalog.resources)
                    or any(resource.system != self._system for resource in catalog.resources)):
                self._finish('invalid_response')
                return
        except (ValueError, TypeError):
            self._finish('invalid_response')
            return
        self._finish('response', catalog)
