"""Experimental account generation handoff; finite policy remains the default."""

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Literal

from .account_media_channel import media_channels_closed, media_channels_locally_retired
from .account_policy import AccountPolicy, validate_policy
from .account_read_worker import AccountReadWorker, WorkerSummary
from .broker_mailbox import BoundReadOutcome, BrokerMailbox, MailboxTicket, ReadOutcome
from .broker_read_socket import ReadSocket, ReadSpec
from .catalog_read_inbox import CatalogReadTicket
from .catalog_read_job import CatalogReadResult, CatalogReadSpec
from .contracts import CertifiedNode, OnlineDevice
from .lamp_control_specs import LampWriteSpec
from .lamp_read_inbox import LampReadTicket
from .lamp_read_job import LampReadResult, LampReadSpec
from .media_lease_inbox import MediaLeaseInbox
from .media_policy import MediaPolicy
from .media_route_job import MediaRouteSpec
from .model_control_inbox import ControlWriteTicket
from .model_control_job import ControlWriteResult, ControlWriteSpec
from .ptz_authority import PtzAuthority, PtzGrant
from .ptz_inbox import MotionTicket
from .ptz_scheduler import PtzSpec, ScheduledPtzResult
from .siren_pulse_inbox import SirenPulseTicket
from .siren_pulse_job import SirenPulseResult, SirenPulseSpec
from .video_delivery import VideoDelivery


@dataclass(frozen=True, slots=True)
class ReadConnection:
    node: CertifiedNode = field(repr=False)
    socket: ReadSocket = field(repr=False)
    local_ip: str = field(repr=False)
    local_port: int = field(repr=False)
    target: OnlineDevice | None = field(default=None, repr=False)
    access_id: int | None = field(default=None, repr=False)
    targets: tuple[OnlineDevice, ...] = field(default=(), repr=False)


@dataclass(frozen=True, slots=True)
class ReadBatchSummary:
    worker: WorkerSummary
    outcomes: tuple[ReadOutcome | None, ...] = field(repr=False)


class AccountReadLifecycle:
    """One trusted account, sequential generations, no thread or automatic login.

    The caller retains external account/enrollment reservations across run().
    connect must be bounded and transfer a NEW exclusively owned socket, never
    a cached PTZ socket. Until it returns, it owns all cleanup on failure. Failed
    connect/run leaves this coordinator blocked: no implicit authentication retry.
    This does not yet coordinate legacy production login paths.
    """

    def __init__(self, *, clock: Callable[[], float] = time.monotonic,
                 motion_grants: tuple[PtzGrant, ...] = (),
                 policy: AccountPolicy = AccountPolicy.FINITE, state_reads: bool = False):
        if type(state_reads) is not bool:
            raise ValueError("explicit state-read scope required")
        self._state_reads = state_reads
        validate_policy(policy)
        self._policy = policy
        self._motion_authority = PtzAuthority(motion_grants)
        self._clock = clock
        self._lock = threading.Lock()
        self._state: Literal["idle", "connecting", "active", "draining", "blocked"] = "idle"
        self._generation = 0
        self._worker: AccountReadWorker | None = None
        self._drain = False
        self._stop = False
        self._lease_inbox: MediaLeaseInbox | None = None

    @property
    def state(self) -> str:
        with self._lock:
            return self._state

    def owns_media_leases(self, inbox: MediaLeaseInbox) -> bool:
        with self._lock:
            return (self._state == "active" and self._lease_inbox is inbox
                    and inbox.generation == self._generation)

    def submit(self, device_id: int, path: str, timeout: float = 2.0) -> MailboxTicket:
        with self._lock:
            if self._state != "active" or self._worker is None:
                raise ValueError("account not accepting reads")
            return self._worker.mailbox.submit(device_id, path, timeout)

    def submit_catalog_read(self, spec: CatalogReadSpec) -> CatalogReadTicket:
        with self._lock:
            if self._state != 'active' or self._worker is None or self._worker.catalog_inbox is None:
                raise ValueError('account not accepting catalogue reads')
            return self._worker.catalog_inbox.submit(spec)

    def take_catalog_read(self, ticket: CatalogReadTicket) -> CatalogReadResult | None:
        with self._lock:
            if self._worker is None or self._worker.catalog_inbox is None or ticket.generation != self._generation:
                return None
            return self._worker.catalog_inbox.take(ticket)

    def cancel_catalog_read(self, ticket: CatalogReadTicket) -> None:
        with self._lock:
            if self._worker is not None and self._worker.catalog_inbox is not None and ticket.generation == self._generation:
                self._worker.catalog_inbox.cancel(ticket)

    def submit_siren_pulse(self, spec: SirenPulseSpec) -> SirenPulseTicket:
        with self._lock:
            if self._state != 'active' or self._worker is None or self._worker.siren_inbox is None:
                raise ValueError('account not accepting siren pulse')
            return self._worker.siren_inbox.submit(spec)

    def take_siren_pulse(self, ticket: SirenPulseTicket) -> SirenPulseResult | None:
        with self._lock:
            if self._worker is None or self._worker.siren_inbox is None or ticket.generation != self._generation:
                return None
            return self._worker.siren_inbox.take(ticket)

    def cancel_siren_pulse(self, ticket: SirenPulseTicket) -> None:
        with self._lock:
            if self._worker is not None and self._worker.siren_inbox is not None and ticket.generation == self._generation:
                self._worker.siren_inbox.cancel(ticket)

    def submit_lamp_read(self, spec: LampReadSpec) -> LampReadTicket:
        with self._lock:
            if self._state != "active" or self._worker is None or self._worker.lamp_inbox is None:
                raise ValueError("account not accepting lamp-state reads")
            return self._worker.lamp_inbox.submit(spec)

    def submit_lamp_write(self, spec: LampWriteSpec) -> LampReadTicket:
        with self._lock:
            if self._state != 'active' or self._worker is None or self._worker.lamp_inbox is None:
                raise ValueError('account not accepting lamp changes')
            return self._worker.lamp_inbox.submit_write(spec)

    def submit_control_write(self, spec: ControlWriteSpec) -> ControlWriteTicket:
        with self._lock:
            if self._state != 'active' or self._worker is None or self._worker.control_inbox is None:
                raise ValueError('account not accepting control changes')
            return self._worker.control_inbox.submit(spec)

    def take_control_write(self, ticket: ControlWriteTicket) -> ControlWriteResult | None:
        with self._lock:
            if self._worker is None or self._worker.control_inbox is None or ticket.generation != self._generation:
                return None
            return self._worker.control_inbox.take(ticket)

    def cancel_control_write(self, ticket: ControlWriteTicket) -> None:
        with self._lock:
            if self._worker is not None and self._worker.control_inbox is not None and ticket.generation == self._generation:
                self._worker.control_inbox.cancel(ticket)

    def take_lamp_read(self, ticket: LampReadTicket) -> LampReadResult | None:
        with self._lock:
            if self._worker is None or self._worker.lamp_inbox is None or ticket.generation != self._generation:
                return None
            return self._worker.lamp_inbox.take(ticket)

    def cancel_lamp_read(self, ticket: LampReadTicket) -> None:
        with self._lock:
            if self._worker is not None and self._worker.lamp_inbox is not None and ticket.generation == self._generation:
                self._worker.lamp_inbox.cancel(ticket)

    def submit_motion(self, spec: PtzSpec, *, generation: int | None = None) -> MotionTicket:
        """Trusted driver callers only; grants are fixed before authentication."""
        self._motion_authority.authorize(spec)
        with self._lock:
            if (self._state != "active" or self._worker is None
                    or self._worker.motion_inbox is None
                    or (generation is not None and
                        (type(generation) is not int or generation != self._generation))):
                raise ValueError("account not accepting motion")
            return self._worker.motion_inbox.submit(spec)

    def is_active_generation(self, generation: int) -> bool:
        with self._lock:
            return type(generation) is int and self._state == "active" and generation == self._generation

    def take_bound(self, ticket: MailboxTicket) -> BoundReadOutcome | None:
        with self._lock:
            if ticket.generation != self._generation or self._worker is None:
                return None
            return self._worker.mailbox.take_bound(ticket)

    def take_motion(self, ticket: MotionTicket) -> ScheduledPtzResult | None:
        with self._lock:
            if (ticket.generation != self._generation or self._worker is None
                    or self._worker.motion_inbox is None):
                return None
            return self._worker.motion_inbox.take(ticket)

    def cancel_motion(self, ticket: MotionTicket) -> bool:
        with self._lock:
            if (ticket.generation != self._generation or self._worker is None
                    or self._worker.motion_inbox is None):
                return False
            return self._worker.motion_inbox.cancel(ticket)

    def abandon_motion(self, ticket: MotionTicket) -> None:
        with self._lock:
            if (ticket.generation == self._generation and self._worker is not None
                    and self._worker.motion_inbox is not None):
                self._worker.motion_inbox.abandon(ticket)

    def take(self, ticket: MailboxTicket) -> ReadOutcome | None:
        with self._lock:
            if ticket.generation != self._generation or self._worker is None:
                return None
            return self._worker.mailbox.take(ticket)

    def cancel(self, ticket: MailboxTicket) -> bool:
        with self._lock:
            if ticket.generation != self._generation or self._worker is None:
                return False
            return self._worker.mailbox.cancel(ticket)

    def drain(self) -> None:
        """Immediately reject admission, then let the sole reader drain <=2s."""
        with self._lock:
            if self._state not in ("connecting", "active", "draining"):
                return
            self._state, self._drain = "draining", True
            if self._worker is not None:
                self._worker.mailbox.drain()
                self._worker.drain()

    def stop(self) -> None:
        with self._lock:
            if self._state not in ("connecting", "active", "draining"):
                return
            self._state, self._stop = "draining", True
            if self._worker is not None:
                self._worker.mailbox.close()
                self._worker.stop()

    def run(self, connect: Callable[[int], ReadConnection], *,
            cancelled: Callable[[], bool] = lambda: False) -> WorkerSummary:
        """One explicit session; cancellation drains cleanup, never reconnects.

        The trusted connection factory must also observe cancellation during
        authentication. The worker observes it after ownership transfer.
        """
        return self._run(connect, (), cancelled).worker

    def run_reads(self, connect: Callable[[int], ReadConnection], reads: tuple[ReadSpec, ...], *,
                  cancelled: Callable[[], bool] = lambda: False) -> ReadBatchSummary:
        """Trusted bounded bootstrap batch; retain results before admitting replacement."""
        if not isinstance(reads, tuple) or not 1 <= len(reads) <= 8:
            raise ValueError("bounded initial read tuple required")
        # Reuse admission validation before claiming/authenticating. No wire I/O.
        validation = BrokerMailbox(1, state_reads=self._state_reads)
        for spec in reads:
            if not isinstance(spec, ReadSpec):
                raise ValueError("invalid initial read specification")
            validation.submit(spec.device_id, spec.path, spec.timeout)
        validation.close()
        del validation
        return self._run(connect, reads, cancelled)

    def run_media(self, connect: Callable[[int], ReadConnection], *,
                  media: Callable[[ReadConnection], MediaRouteSpec],
                  delivery: VideoDelivery,
                  cancelled: Callable[[], bool]) -> WorkerSummary:
        """Explicit continuous media on the same account/sole reader, no motion.

        The driver builds its exact-device route from the authenticated connection.
        Caller owns the decoder thread; the worker only hands off bounded frames.
        """
        if (self._policy is not AccountPolicy.CONTINUOUS or self._motion_authority.enabled
                or not isinstance(delivery, VideoDelivery) or delivery.closed):
            raise ValueError("continuous media-only account and fresh delivery required")
        return self._run(connect, (), cancelled, media=media, delivery=delivery).worker

    def run_media_motion(self, connect: Callable[[int], ReadConnection], *,
                         media: Callable[[ReadConnection], MediaRouteSpec],
                         delivery: VideoDelivery, cancelled: Callable[[], bool]) -> WorkerSummary:
        """Explicit driver grants only; no bootstrap gesture or inferred permission.

        Queued gestures cannot start before TCP media readiness. STOP is drained
        before media/route teardown, and any unconfirmed release blocks reuse.
        """
        if (self._policy is not AccountPolicy.CONTINUOUS or not self._motion_authority.enabled
                or not isinstance(delivery, VideoDelivery) or delivery.closed):
            raise ValueError("reviewed continuous motion grants and fresh media delivery required")
        return self._run(connect, (), cancelled, media=media, delivery=delivery,
                         media_motion=True).worker

    def run_media_leases(self, connect: Callable[[int], ReadConnection], *,
                         leases: Callable[[ReadConnection, int], MediaLeaseInbox],
                         cancelled: Callable[[], bool]) -> WorkerSummary:
        """Trusted owner transfers one authenticated, account-bound lease inbox.

        No worker thread or authentication is created per source. Reviewed motion
        also requires a ready lease for its exact device and STOP before detach.
        """
        if self._policy is not AccountPolicy.CONTINUOUS:
            raise ValueError("continuous media-lease account required")
        return self._run(connect, (), cancelled, leases_factory=leases).worker

    def _run(self, connect: Callable[[int], ReadConnection], reads: tuple[ReadSpec, ...],
             cancelled: Callable[[], bool], *,
             media: Callable[[ReadConnection], MediaRouteSpec] | None = None,
             delivery: VideoDelivery | None = None, media_motion: bool = False,
             leases_factory: Callable[[ReadConnection, int], MediaLeaseInbox] | None = None) -> ReadBatchSummary:
        """Claim before auth; permit replacement only after verified physical close.

        No I/O under the lifecycle lock. Old results are retained only until the
        next explicit run; old tickets never address the next generation. Nothing
        is resubmitted, and no historical result queue accumulates.
        """
        with self._lock:
            if self._state != "idle":
                raise ValueError("account busy or cleanup unconfirmed")
            if self._generation >= (1 << 64) - 1:
                self._state = "blocked"
                raise ValueError("account generation exhausted")
            self._generation += 1
            generation = self._generation
            self._state, self._worker = "connecting", None
            self._drain = self._stop = False

        connection = None
        worker = None
        transferred = False
        summary = None
        inbox: MediaLeaseInbox | None = None
        try:
            connection = connect(generation)
            if leases_factory is not None:
                inbox = leases_factory(connection, generation)
                if (not isinstance(inbox, MediaLeaseInbox) or inbox.generation != generation
                        or connection.access_id is None or not inbox.matches_account(connection.access_id)
                        or not inbox.matches_targets(connection.targets)):
                    raise ValueError("authenticated account lease inbox required")
            worker = AccountReadWorker(connection.node, generation, connection.local_ip,
                                       connection.local_port, clock=self._clock,
                                       allow_motion=self._motion_authority.enabled,
                policy=self._policy, state_reads=self._state_reads,
                media_capacity=inbox.capacity if inbox is not None else 1)
            spec = media(connection) if media is not None else None
            if media is not None and not isinstance(spec, MediaRouteSpec):
                raise ValueError("typed authenticated media route required")
            tickets = tuple(worker.mailbox.submit(r.device_id, r.path, r.timeout) for r in reads)
            with self._lock:
                self._worker = worker
                self._lease_inbox = inbox
                if self._stop:
                    worker.mailbox.close()
                    worker.stop()
                elif self._drain:
                    worker.mailbox.drain()
                    worker.drain()
                else:
                    self._state = "active"
            transferred = True
            if inbox is not None:
                summary = worker.run(connection.socket, cancelled=cancelled,
                    media_leases=inbox, with_media_tcp=True, media_policy=MediaPolicy.CONTINUOUS,
                    allow_media_motion=self._motion_authority.enabled)
            elif media is None:
                summary = worker.run(connection.socket, cancelled=cancelled)
            else:
                if media_motion:
                    summary = worker.run(connection.socket, cancelled=cancelled,
                        initial_media=spec, with_media_tcp=True, video_delivery=delivery,
                        media_policy=MediaPolicy.CONTINUOUS, allow_media_motion=True)
                else:
                    summary = worker.run(connection.socket, cancelled=cancelled,
                        initial_media=spec, with_media_tcp=True, video_delivery=delivery,
                        media_policy=MediaPolicy.CONTINUOUS)
            return ReadBatchSummary(summary, tuple(worker.mailbox.take(t) for t in tickets))
        finally:
            try:
                if connection is not None and not transferred:
                    if worker is not None:
                        worker.stop()
                        worker.run(connection.socket)
                    else:
                        connection.socket.close()
            finally:
                with self._lock:
                    self._lease_inbox = None
                    # An exception has no verified WorkerSummary. Fail closed,
                    # including interruptions even if the worker attempted close.
                    self._state = ("idle" if summary is not None
                                   and not summary.socket_close_failed and not summary.lease_cleanup_uncertain
                                   and (media_channels_closed(summary.media_channels, require_tcp_receipt=inbox is None)
                                        or (inbox is not None and summary.media_account_retired
                                            and media_channels_locally_retired(summary.media_channels)))
                                   and (summary.media is None or not summary.media.opened
                                        or summary.media.broker_release)
                                   and (summary.tcp is None or (not summary.tcp.socket_close_failed
                                        and (summary.tcp.close_acknowledged or inbox is not None)))
                                   and (summary.motion is None or not summary.motion.start_attempted
                                        or summary.motion.release_delivery_confirmed)
                                   and (summary.siren is None or not summary.siren.start_attempted
                                        or summary.siren.off_confirmed) else "blocked")
                if delivery is not None:
                    delivery.close()
                if isinstance(inbox, MediaLeaseInbox):
                    inbox.close()
