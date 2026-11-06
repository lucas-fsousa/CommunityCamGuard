"""Per-camera video/control binding to an existing account; never logs in."""

import threading
import time
from collections.abc import Callable

from ...camera_identity import valid_camera_id
from ...db import p2p
from ...db.p2p import P2PEnrollment
from .p2p.account_media_channel import MediaBootstrap
from .p2p.account_read_lifecycle import AccountReadLifecycle
from .p2p.contracts import OnlineDevice, P2PProbeError
from .p2p.media_lease_inbox import MediaLeaseInbox, MediaLeaseOutcome, MediaLeaseTicket
from .p2p.media_route_job import MediaRouteSpec
from .p2p.ptz_authority import PtzAuthority, PtzGrant
from .p2p.video_delivery import VideoDelivery
from .shared_device_controls import SharedDeviceControls
from .shared_read_session import SharedReadResult


class LeasedReadSession(SharedDeviceControls):
    """One source caller owns its lease, not the account lifecycle or UDP socket.

    Controls remain exact-device delegates on that lifecycle. No motion grant is
    inferred here. The shared owner must retain its global SDK exclusion until
    all camera leases and account cleanup have actually completed.
    """

    def __init__(self, entry: P2PEnrollment, target: OnlineDevice,
                 lifecycle: AccountReadLifecycle, inbox: MediaLeaseInbox, *,
                 current_enrollment: Callable[[], P2PEnrollment | None] | None = None,
                 motion_grants: tuple[PtzGrant, ...] = (), motion_valid: Callable[[], bool] | None = None,
                 clock: Callable[[], float] = time.monotonic,
                 wait: Callable[[float], None] = time.sleep):
        if (not entry.camera_id or not valid_camera_id(entry.camera_id)
                or entry.device_id != str(target.device_id)
                or not inbox.matches_account(entry.access_id) or not inbox.permits_target(target)):
            raise ValueError("exact enrolled and authenticated lease target required")
        PtzAuthority(motion_grants)
        if (len(motion_grants) > 1 or any(grant.access_id != entry.access_id
                or str(grant.device_id) != entry.device_id for grant in motion_grants)
                or (motion_valid is not None and (not callable(motion_valid) or not motion_grants))):
            raise ValueError("exact per-device reviewed motion grant required")
        self._camera_id, self._device_id = entry.camera_id, entry.device_id
        self._entry, self._target = entry, target
        self._lifecycle, self._inbox = lifecycle, inbox
        self._current = current_enrollment or (lambda: p2p.read_registered_enrollment(self._camera_id))
        self._clock, self._wait = clock, wait
        self._active_entry: P2PEnrollment | None = None
        self._motion_grants = motion_grants
        self._motion_valid = motion_valid
        self._stop = threading.Event()
        self._claim = threading.Lock()
        self._started = False
        self._ticket: MediaLeaseTicket | None = None
        self._outcome: MediaLeaseOutcome | None = None

    def stop(self) -> None:
        self._stop.set()
        with self._claim:
            ticket = self._ticket
        if ticket is not None:
            self._inbox.cancel(ticket)

    def matches_enrollment(self, entry: P2PEnrollment) -> bool:
        return (not self._stop.is_set() and super().matches_enrollment(entry)
                and self._lifecycle.owns_media_leases(self._inbox))

    def diagnostics(self) -> dict[str, str | bool]:
        outcome = self._outcome
        channel = outcome.channel if outcome is not None else None
        return {"session_scope": "media_lease", "session_worker": "leased",
                "session_lease_detached": outcome is not None and outcome.status in {"closed", "cancelled"},
                # Detaching a source is NOT physical closure of the shared socket.
                "session_broker_socket_closed": False,
                "session_broker_release": channel is not None and channel.media is not None and channel.media.broker_release,
                "session_tcp_socket_closed": channel is not None and channel.tcp is not None and not channel.tcp.socket_close_failed,
                "session_tcp_receipt": channel is not None and channel.tcp is not None and channel.tcp.close_acknowledged}

    def run(self, *, cancelled: Callable[[], bool],
            video_delivery: VideoDelivery | None = None) -> SharedReadResult:
        if not isinstance(video_delivery, VideoDelivery) or video_delivery.closed:
            raise ValueError("fresh per-camera media delivery required")
        with self._claim:
            if self._started:
                raise ValueError("leased read session already started")
            self._started = True
        try:
            if self._stop.is_set() or cancelled():
                return SharedReadResult("stopped")
            if not self._lifecycle.owns_media_leases(self._inbox):
                return SharedReadResult("busy")
            if self._current() != self._entry:
                return SharedReadResult("enrollment_changed")
            ticket = self._inbox.submit(self._camera_id,
                MediaBootstrap(MediaRouteSpec(self._entry.access_id, self._target), video_delivery))
            with self._claim:
                self._ticket = ticket
            self._active_entry = self._entry
            return self._await_retirement(ticket, cancelled)
        finally:
            self._active_entry = None
            video_delivery.close()

    def _await_retirement(self, ticket: MediaLeaseTicket,
                          cancelled: Callable[[], bool]) -> SharedReadResult:
        deadline: float | None = None
        reason = ""
        failure: BaseException | None = None
        next_check = self._clock() + 1
        while True:
            outcome = self._inbox.take(ticket)
            if outcome is not None:
                self._outcome = outcome
                if outcome.status == "blocked":
                    raise P2PProbeError("native media lease cleanup unconfirmed")
                if failure is not None:
                    raise failure
                ended = outcome.channel.failure if outcome.channel is not None else ""
                if not reason and self._stop.is_set():
                    reason = "stopped"
                return SharedReadResult(reason or (ended if ended in {"socket", "protocol"} else "media_complete"))
            if deadline is None:
                try:
                    if self._stop.is_set() or cancelled():
                        reason = "stopped"
                    elif not self._lifecycle.owns_media_leases(self._inbox):
                        reason = "account_ended"
                    elif self._clock() >= next_check:
                        next_check = self._clock() + 1
                        if self._current() != self._entry:
                            reason = "enrollment_changed"
                except BaseException as exc:
                    failure, reason = exc, "stopped"
                if reason:
                    self._inbox.cancel(ticket)
                    deadline = self._clock() + 5
            if deadline is not None and self._clock() >= deadline:
                raise P2PProbeError("native media lease cleanup deadline exceeded")
            self._wait(0.01)
