"""Strict type-12 status-only entry point, retained for existing read callers."""

import time
from collections.abc import Callable

from .broker_sequences import BrokerSequences
from .contracts import CertifiedNode
from .lamp_control_specs import LampReadResult, LampReadSpec
from .lamp_exchange_job import LampExchangeJob

__all__ = ('LampReadJob', 'LampReadResult', 'LampReadSpec')


class LampReadJob(LampExchangeJob):
    def __init__(self, node: CertifiedNode, sequences: BrokerSequences, spec: LampReadSpec, *,
                 generation: int, deadline: float, clock: Callable[[], float] = time.monotonic):
        if not isinstance(spec, LampReadSpec):
            raise ValueError('lamp read requires a read-only spec')
        super().__init__(node, sequences, spec, generation=generation, deadline=deadline, clock=clock)
