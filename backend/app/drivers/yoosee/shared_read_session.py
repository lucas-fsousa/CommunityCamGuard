"""On-demand continuous read session under the server's legacy exclusion boundary.

Not a periodic warmup or public endpoint. The server supplies its shutdown
predicate and owns the calling thread. Optional media requires an explicit bounded
delivery queue. Motion remains disabled unless the driver supplies an exact-device
grant; queued gestures wait for TCP readiness. No generic writes or decoder run here.
"""

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass

from ...db import p2p
from ...db.p2p import P2PEnrollment
from ..contracts import ControlNotReady
from .native_ptz import reserve_read_handoff
from .p2p.account_policy import AccountPolicy
from .p2p.account_read_connect import ReadConnectFailure, open_read_connection
from .p2p.account_read_lifecycle import AccountReadLifecycle, ReadConnection
from .p2p.account_read_worker import WorkerSummary
from .p2p.broker_mailbox import MailboxTicket, ReadOutcome
from .p2p.contracts import P2PProbeError
from .p2p.control_ownership import ControlChannelBusy, control_channel_contended
from .p2p.lamp_read_inbox import LampReadTicket
from .p2p.lamp_read_job import LampReadResult, LampReadSpec
from .p2p.media_route_job import MediaRouteSpec
from .p2p.ptz_authority import PtzGrant
from .p2p.ptz_inbox import MotionTicket
from .p2p.ptz_scheduler import PtzSpec, ScheduledPtzResult
from .p2p.video_delivery import VideoDelivery


@dataclass(frozen=True, slots=True)
class SharedReadResult:
    reason: str
    worker: WorkerSummary | None = None


class SharedReadSession:
    """One explicit run; no automatic restart after yielding or losing credentials.

    Uses account exclusion, NOT an indefinite per-camera operation lock: the latter
    would prevent foreground requests reaching the contention signal. Enrollment
    is checked before authentication and at most once/second thereafter. Stop and
    foreground demand are checked each pump iteration, including during opening.
    """

    def __init__(self, camera_id: str, device_id: str, *,
                 clock: Callable[[], float] = time.monotonic, state_reads: bool = False,
                 motion_grants: tuple[PtzGrant, ...] = (),
                 motion_valid: Callable[[], bool] | None = None):
        if (not camera_id or not device_id or len(device_id) > 20
                or not device_id.isascii() or not device_id.isdecimal()
                or not 0 < int(device_id) < 1 << 64):
            raise ValueError("shared read target required")
        self._camera_id, self._device_id, self._clock = camera_id, device_id, clock
        if (not isinstance(motion_grants, tuple) or len(motion_grants) > 1
                or any(not isinstance(grant, PtzGrant) or grant.device_id != int(device_id)
                       for grant in motion_grants)):
            raise ValueError("motion grant must match this exact media device")
        self._motion_grants = motion_grants
        if motion_valid is not None and (not callable(motion_valid) or not motion_grants):
            raise ValueError("motion revalidation requires an explicit grant")
        self._motion_valid = motion_valid
        self._lifecycle = AccountReadLifecycle(clock=clock, policy=AccountPolicy.CONTINUOUS,
                                               state_reads=state_reads, motion_grants=motion_grants)
        self._active_entry: P2PEnrollment | None = None
        self._stop = threading.Event()
        self._claim = threading.Lock()
        self._started = False
        self._last_worker: WorkerSummary | None = None
        self._connection_failure = ""

    def diagnostics(self) -> dict[str, str | bool]:
        """Fixed cleanup facts, including failures that cannot return a successful result."""
        worker = self._last_worker
        if worker is None:
            return {"session_worker": "unavailable", **(
                {"session_connection_failure": self._connection_failure} if self._connection_failure else {})}
        return {"session_worker": worker.reason,
                "session_protocol_site": worker.protocol_site,
                "session_broker_socket_closed": not worker.socket_close_failed,
                "session_broker_release": worker.media is None or worker.media.broker_release,
                "session_tcp_receipt": worker.tcp is None or worker.tcp.close_acknowledged,
                "session_tcp_socket_closed": worker.tcp is None or not worker.tcp.socket_close_failed,
                "session_motion_started": worker.motion is not None and worker.motion.start_attempted,
                "session_motion_cleanup_confirmed": worker.motion is None or not worker.motion.start_attempted
                    or worker.motion.release_delivery_confirmed}

    def stop(self) -> None:
        self._stop.set()
        self._lifecycle.stop()

    def submit(self, path: str) -> MailboxTicket:
        """Only the fixed identity/state read scope; no caller device ID or write."""
        return self._lifecycle.submit(int(self._device_id), path)

    def take(self, ticket: MailboxTicket) -> ReadOutcome | None:
        return self._lifecycle.take(ticket)

    def cancel(self, ticket: MailboxTicket) -> bool:
        return self._lifecycle.cancel(ticket)

    def matches_enrollment(self, entry: P2PEnrollment) -> bool:
        return self._active_entry == entry and self._lifecycle.state == "active"

    def submit_lamp_read(self) -> LampReadTicket:
        entry = self._active_entry
        if entry is None:
            raise ValueError("active lamp-read enrollment required")
        return self._lifecycle.submit_lamp_read(LampReadSpec(entry.access_id, int(entry.device_id)))

    def take_lamp_read(self, ticket: LampReadTicket) -> LampReadResult | None:
        return self._lifecycle.take_lamp_read(ticket)

    def cancel_lamp_read(self, ticket: LampReadTicket) -> None:
        self._lifecycle.cancel_lamp_read(ticket)

    def submit_motion(self, direction: str) -> MotionTicket:
        entry = self._active_entry
        if entry is None or not self._motion_grants:
            raise ValueError("reviewed active media motion scope required")
        grant = self._motion_grants[0]
        if grant.access_id != entry.access_id:
            raise ValueError("media motion enrollment changed")
        # Local evidence only; never authenticate/refresh from an admission check.
        if self._motion_valid is not None and self._motion_valid() is not True:
            raise ValueError("media motion evidence is no longer current")
        return self._lifecycle.submit_motion(PtzSpec(grant.access_id, grant.device_id,
                                                    direction, grant.directions))

    def take_motion(self, ticket: MotionTicket) -> ScheduledPtzResult | None:
        return self._lifecycle.take_motion(ticket)

    def cancel_motion(self, ticket: MotionTicket) -> bool:
        return self._lifecycle.cancel_motion(ticket)

    def abandon_motion(self, ticket: MotionTicket) -> None:
        self._lifecycle.abandon_motion(ticket)

    def run(self, *, cancelled: Callable[[], bool],
            video_delivery: VideoDelivery | None = None) -> SharedReadResult:
        if self._motion_grants and video_delivery is None:
            raise ValueError("motion scope requires explicit native media delivery")
        if video_delivery is not None and (not isinstance(video_delivery, VideoDelivery)
                                           or video_delivery.closed):
            raise ValueError("fresh media delivery required")
        with self._claim:
            if self._started:
                raise ValueError("shared read session already started")
            self._started = True
        try:
            if self._stop.is_set() or cancelled():
                return SharedReadResult("stopped")
            try:
                with reserve_read_handoff(wait=False):
                    return self._run_owned(cancelled, video_delivery)
            except (ControlChannelBusy, ControlNotReady):
                return SharedReadResult("busy")
        finally:
            self._active_entry = None
            if video_delivery is not None:
                video_delivery.close()

    def _run_owned(self, cancelled: Callable[[], bool],
                   delivery: VideoDelivery | None) -> SharedReadResult:
        try:
            entry = p2p.read_registered_enrollment(self._camera_id)
        except Exception:
            return SharedReadResult("registry_unavailable")
        if (entry is None or entry.camera_id != self._camera_id
                or entry.device_id != self._device_id):
            return SharedReadResult("enrollment_changed")
        if self._motion_grants and self._motion_grants[0].access_id != entry.access_id:
            return SharedReadResult("motion_scope_changed")
        self._active_entry = entry
        reason: str | None = None
        next_check = self._clock()  # Recheck immediately before opening.

        def should_yield() -> bool:
            nonlocal reason, next_check
            if reason is not None:
                return True
            if self._stop.is_set() or cancelled():
                reason = "stopped"
            elif delivery is not None and delivery.closed:
                reason = "consumer_closed"
            elif control_channel_contended():
                reason = "contended"
            elif self._clock() >= next_check:
                next_check = self._clock() + 1
                try:
                    current = p2p.read_registered_enrollment(self._camera_id)
                    # Compare actual credentials too: timestamps only have second precision.
                    if current != entry:
                        reason = "enrollment_changed"
                except Exception:
                    reason = "registry_unavailable"
            return reason is not None

        if should_yield():
            return SharedReadResult(reason or "stopped")

        def connect(_: int) -> ReadConnection:
            return open_read_connection(entry, camera_id=self._camera_id,
                                        device_id=self._device_id, cancelled=should_yield)

        def media(connection: ReadConnection) -> MediaRouteSpec:
            target = connection.target
            if target is None or str(target.device_id) != self._device_id or not target.status:
                raise ValueError("shared media authenticated target mismatch")
            return MediaRouteSpec(entry.access_id, target)

        try:
            if delivery is None:
                result = self._lifecycle.run(connect, cancelled=should_yield)
            else:
                if self._motion_grants:
                    result = self._lifecycle.run_media_motion(connect, media=media, delivery=delivery,
                                                             cancelled=should_yield)
                else:
                    result = self._lifecycle.run_media(connect, media=media, delivery=delivery,
                                                       cancelled=should_yield)
        except ReadConnectFailure as error:
            self._connection_failure = error.reason
            return SharedReadResult(reason or "connection")
        self._last_worker = result
        if self._lifecycle.state == "blocked":
            raise P2PProbeError("shared read/media cleanup unconfirmed")
        return SharedReadResult(reason or result.reason, result)
