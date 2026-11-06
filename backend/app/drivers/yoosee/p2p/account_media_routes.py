"""Bounded route ownership under one account and one sequence allocator; no I/O."""

import secrets
import time
from collections.abc import Callable

from .account_output_budget import AccountOutputBudget
from .account_policy import validate_media_capacity
from .broker_sequences import BrokerSequences
from .contracts import CertifiedNode, OnlineDevice
from .media_route_job import MediaRouteJob, MediaRouteSpec


class AccountMediaRoutes:
    """One route per device; unconfirmed release fences the entire account.

    Sole-reader use only. This does not admit multiple native sources or create
    TCP transports. Owners must finish each TCP channel before cancelling its
    broker route. Only confirmed/unopened terminal routes can release a slot.
    """

    def __init__(self, node: CertifiedNode, sequences: BrokerSequences, *,
                 generation: int, local_ip: str, local_port: int, capacity: int = 1,
                 clock: Callable[[], float] = time.monotonic):
        validate_media_capacity(capacity)
        self._node: CertifiedNode | None = node
        self._sequences = sequences
        self._generation = generation
        self._local = local_ip, local_port
        self._capacity, self._clock = capacity, clock
        self._routes: dict[int, MediaRouteJob] = {}
        self._closed = False
        self._output: AccountOutputBudget | None = None
        self._next_link = secrets.randbelow(0xFFFFFF) + 1
        self._links_left = 0xFFFFFF

    @property
    def cleanup_pending(self) -> bool:
        return any(job.result is None for job in self._routes.values())

    @property
    def release_uncertain(self) -> bool:
        return any(job.result is not None and job.result.opened
                   and not job.result.broker_release for job in self._routes.values())

    def prepare(self, spec: MediaRouteSpec, output: AccountOutputBudget) -> MediaRouteJob:
        if self._closed or self.release_uncertain:
            raise ValueError("account not accepting media route")
        # Preserve uncertain jobs; never evict an OPEN awaiting its CLOSE receipt.
        for device_id, job in tuple(self._routes.items()):
            if job.result is not None:
                del self._routes[device_id]
        if not isinstance(spec, MediaRouteSpec) or not isinstance(spec.device, OnlineDevice):
            raise ValueError("typed media route required")
        if self._output is not None and output is not self._output:
            raise ValueError("shared account output owner required")
        device_id = spec.device.device_id
        if device_id in self._routes or len(self._routes) >= self._capacity:
            raise ValueError("media route capacity or duplicate device")
        if not self._links_left:
            raise ValueError("media route link space exhausted")
        link_id = self._next_link
        self._next_link = link_id % 0xFFFFFF + 1
        self._links_left -= 1
        assert self._node is not None
        job = MediaRouteJob(self._node, self._sequences, output, spec,
            generation=self._generation, local_ip=self._local[0], local_port=self._local[1],
            clock=self._clock, link_id=link_id)
        # A3 correlates by link ID, not device ID. The scalar allocator never
        # reuses a link in this generation, even after confirmed route cleanup.
        self._routes[device_id] = job
        self._output = output
        return job

    def observe(self, plain: bytes, peer: tuple[str, int], *, generation: int) -> None:
        for job in self._routes.values():
            job.observe(plain, peer, generation=generation)

    def cancel_all(self) -> None:
        self._closed = True
        for job in self._routes.values():
            job.cancel()
        self._node = None
