"""Strict type-11 boolean-only entry point, never usable from the status reader."""

import time
from collections.abc import Callable

from .broker_sequences import BrokerSequences
from .contracts import CertifiedNode
from .lamp_control_specs import LampWriteSpec
from .lamp_exchange_job import LampExchangeJob


class LampWriteJob(LampExchangeJob):
    def __init__(self, node: CertifiedNode, sequences: BrokerSequences, spec: LampWriteSpec, *,
                 generation: int, deadline: float, clock: Callable[[], float] = time.monotonic):
        if not isinstance(spec, LampWriteSpec):
            raise ValueError('lamp write requires a binary write spec')
        super().__init__(node, sequences, spec, generation=generation, deadline=deadline, clock=clock)
