"""Finite driver collection on the running sole-reader account; no socket calls."""

import time
from collections.abc import Callable

from ...db.p2p import P2PEnrollment
from .motion_evidence import VerifiedMotionScope, grant_from_completions
from .native_ptz_policy import PtzProfile
from .p2p.account_read_lifecycle import AccountReadLifecycle
from .p2p.broker_mailbox import BoundReadOutcome, MailboxTicket
from .p2p.contracts import OnlineDevice
from .p2p.ptz_inbox import MotionTicket
from .p2p.ptz_scheduler import PtzSpec

_PATHS = ("ProConst._productInfo", "ProConst._versionInfo", "ProReadonly.devInfo")


class MotionCollection:
    """One caller polls three reads then submits at most one verified gesture.

    Caller retains exact enrollment and account/legacy-login exclusion for the
    entire active lifecycle. Online inventory must belong to that same session.
    No thread, socket reader, authentication, retry or capability persistence.
    Always close the collector when abandoning it; this only cancels its reads.
    """

    def __init__(self, lifecycle: AccountReadLifecycle, enrollment: P2PEnrollment, *,
                 camera_id: str, profile: PtzProfile, online: OnlineDevice,
                 clock: Callable[[], float] = time.monotonic):
        if (not camera_id or enrollment.camera_id != camera_id
                or not isinstance(online, OnlineDevice) or type(online.device_id) is not int
                or str(online.device_id) != enrollment.device_id
                or type(online.status) is not int or online.status <= 0):
            raise ValueError("online motion target does not match enrollment")
        self._owner, self._entry = lifecycle, enrollment
        self._camera, self._profile, self._clock = camera_id, profile, clock
        self._started = clock()
        self._tickets: list[MailboxTicket] = []
        self._results: dict[MailboxTicket, BoundReadOutcome] = {}
        self._scope: VerifiedMotionScope | None = None
        self._closed = self._submitted = False
        try:
            for path in _PATHS:
                ticket = lifecycle.submit(online.device_id, path, 2)
                self._tickets.append(ticket)
                if ticket.generation != self._tickets[0].generation:
                    raise ValueError("account generation changed during collection admission")
        except BaseException:
            self.close()
            raise

    def close(self) -> None:
        self._closed = True
        for ticket in self._tickets:
            self._owner.cancel(ticket)
            self._owner.take_bound(ticket)
        self._results.clear()
        self._scope = None

    def poll(self) -> bool:
        if self._closed:
            raise ValueError("motion collection closed")
        try:
            generation = self._tickets[0].generation
            if (not self._owner.is_active_generation(generation)
                    or not 0 <= self._clock() - self._started < 10):
                raise ValueError("motion collection expired or account changed")
            if self._scope is not None:
                self._scope.for_generation(generation, clock=self._clock)
                return True
            for ticket in self._tickets:
                if ticket in self._results:
                    continue
                bound = self._owner.take_bound(ticket)
                if bound is None:
                    continue
                if (bound.request is None or bound.outcome.status != "response"
                        or bound.outcome.completion is None):
                    raise ValueError("motion collection did not receive camera evidence")
                self._results[ticket] = bound
            if len(self._results) != 3:
                return False
            results = [self._results[ticket] for ticket in self._tickets]
            requests = tuple(result.request for result in results if result.request is not None)
            completions = tuple(result.outcome.completion for result in results
                                if result.outcome.completion is not None)
            self._scope = grant_from_completions(self._entry, camera_id=self._camera,
                profile=self._profile, generation=generation, requests=requests,
                completions=completions, started_at=self._started, clock=self._clock)
            self._results.clear()  # Do not retain decoded identity/axis payloads.
            return True
        except BaseException:
            self.close()
            raise

    def submit(self, direction: str, duration: float = 0.2) -> MotionTicket:
        if self._closed or self._submitted or self._scope is None:
            raise ValueError("motion evidence not ready or already consumed")
        generation = self._scope.generation
        grant = self._scope.for_generation(generation, clock=self._clock)
        if direction not in grant.directions:
            raise ValueError("direction lacks verified current axis support")
        # Expected generation is checked atomically with admission, after polling.
        ticket = self._owner.submit_motion(PtzSpec(grant.access_id, grant.device_id,
            direction, grant.directions, duration), generation=generation)
        self._submitted = True
        return ticket
