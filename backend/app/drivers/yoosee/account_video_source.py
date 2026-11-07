"""Lazy reviewed source: wait for the server-owned account, then borrow a lease."""

import sqlite3
import threading
import time
from collections.abc import Callable

from ...db.errors import is_lock_busy
from ...db.p2p import P2PEnrollment
from ...media.mp4_fanout import StreamClosedError, Subscription
from ...media.source_registry import SourceCleanupUnconfirmed
from .leased_read_session import LeasedReadSession
from .native_account_service import NativeAccounts
from .native_cohort import NativeCohort
from .native_video_source import NativeVideoResult, NativeVideoSource
from .p2p.ptz_authority import PtzGrant
from .shared_read_session import SharedReadResult


class AccountVideoSource:
    """Construction/subscription do not authenticate or create worker threads.

    Only run asks the retained server service for its account. Cancellation while
    waiting owns no camera lease/decoder; the account remains owned by the server.
    """

    def __init__(self, entry: P2PEnrollment, accounts: NativeAccounts, *,
                 factory: Callable[[LeasedReadSession], NativeVideoSource],
                 valid: Callable[[], bool], motion_grants: tuple[PtzGrant, ...] = (),
                 cohort: NativeCohort | None = None,
                 clock: Callable[[], float] = time.monotonic):
        self._entry, self._accounts = entry, accounts
        self._factory, self._valid, self._grants, self._clock = factory, valid, motion_grants, clock
        if cohort is not None and entry not in cohort.entries:
            raise ValueError("source must belong to the explicit native cohort")
        self._cohort = cohort
        self._lock = threading.Lock()
        self._started = False
        self._current: NativeVideoSource | None = None
        self._unadmitted_retired = False
        self._connection_failure = ""

    @property
    def current(self) -> NativeVideoSource | None:
        with self._lock:
            return self._current

    @property
    def recording(self) -> bool:
        source = self.current
        return source is not None and source.recording

    def subscribe(self) -> Subscription:
        source = self.current
        if source is None:
            raise StreamClosedError("native account is preparing")
        return source.subscribe()

    def diagnostics(self) -> dict[str, str | bool]:
        source = self.current
        if source is not None:
            return source.diagnostics()
        return {"session_scope":"media_lease", "session_worker":"leased",
                "session_lease_detached":self._unadmitted_retired,
                "session_broker_socket_closed":False, "consumer_alive":False,
                "cleanup_failed":False, "session_connection_failure":self._connection_failure}

    def _without_lease(self, reason: str) -> NativeVideoResult:
        self._unadmitted_retired = True
        return NativeVideoResult(reason, SharedReadResult(reason))

    def run(self, *, cancelled: Callable[[], bool]) -> NativeVideoResult:
        try:
            return self._run(cancelled=cancelled)
        except sqlite3.OperationalError as error:
            if not is_lock_busy(error):
                raise
            source = self.current
            if source is not None:
                details = source.diagnostics()
                if (details.get("session_lease_detached") is not True
                        or details.get("consumer_alive") is not False
                        or details.get("cleanup_failed") is not False):
                    raise  # Never convert uncertainty about an active child into a retry.
            else:
                self._unadmitted_retired = True  # No camera lease or decoder was created.
            return NativeVideoResult("busy", SharedReadResult("busy"))

    def _run(self, *, cancelled: Callable[[], bool]) -> NativeVideoResult:
        with self._lock:
            if self._started:
                raise ValueError("account-bound video source already started")
            self._started = True
        if cancelled():
            return self._without_lease("stopped")
        if not self._valid():
            return self._without_lease("enrollment_changed")
        if self._cohort is not None:
            account = self._accounts.prepare(self._cohort.entries, motion_grants=self._cohort.grants,
                motion_valid=self._cohort.valid if self._cohort.grants else None)
        else:
            account = self._accounts.prepare((self._entry,), motion_grants=self._grants,
                                            motion_valid=(lambda _: self._valid()) if self._grants else None)
        deadline, next_check = self._clock() + 22, self._clock() + 1
        while not account.ready:
            if cancelled():
                return self._without_lease("stopped")
            if self._clock() >= next_check:
                next_check = self._clock() + 1
                if not self._valid():
                    return self._without_lease("enrollment_changed")
            if account.cleanup_confirmed:
                self._connection_failure = account.connection_failure
                return self._without_lease("busy" if account.result_reason == "busy" else "connection")
            if self._clock() >= deadline:
                raise SourceCleanupUnconfirmed("native account preparation did not finish")
            time.sleep(0.01)
        if cancelled():
            return self._without_lease("stopped")
        if not self._valid():
            return self._without_lease("enrollment_changed")
        session = self._accounts.binding(self._entry)
        source = self._factory(session)
        with self._lock:
            self._current = source
        return source.run(cancelled=cancelled)
