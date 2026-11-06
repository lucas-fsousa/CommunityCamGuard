"""Server-owned, lazy account worker. One SDK owner, no per-camera login thread."""

import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager

from ...db.p2p import P2PEnrollment
from ...media.server_owner import ServerOwner
from ...media.source_registry import SourceCleanupUnconfirmed, SourceUnavailable
from ...services.thread_shutdown import join_thread, require_retired
from ..contracts import ControlNotReady
from .leased_read_session import LeasedReadSession
from .native_account import NativeAccount
from .p2p.ptz_authority import PtzAuthority, PtzGrant


class NativeAccounts:
    """No authentication at construction or server startup. Admission is internal.

    A driver must still validate each camera's capability evidence before asking
    for a group. This service is not a dashboard toggle or a capability grant.
    Failed/uncertain owners are retained, never replaced by a second login.
    """

    def __init__(self, owner: ServerOwner):
        if not owner.acquired:
            raise SourceUnavailable("server ownership required for driver account service")
        self._owner = owner
        self._lock = threading.Lock()
        self._shutdown = threading.Lock()
        self._account: NativeAccount | None = None
        self._thread: threading.Thread | None = None
        self._closed = self._stopped = False
        self._intents: dict[str, object] = {}
        self._managed = self._retiring = False

    @contextmanager
    def source_intent(self, entry: P2PEnrollment) -> Iterator[None]:
        """Keep the account across camera retries, not after the last source ends."""
        camera_id = entry.camera_id
        if not camera_id:
            raise SourceUnavailable("enrolled camera identity required")
        token = object()
        with self._lock:
            if self._closed or self._retiring or not self._owner.acquired:
                raise SourceUnavailable("driver account service admission closed")
            if camera_id in self._intents:
                raise ControlNotReady("native source intent already owned or full")
            if not self._managed and self._account is not None:
                raise ControlNotReady("unmanaged native account still owned")
            self._managed = True
            self._intents[camera_id] = token
        try:
            yield
        finally:
            with self._lock:
                if self._intents.get(camera_id) is token:
                    del self._intents[camera_id]
                last = not self._intents
                if last:
                    self._retiring = True
            if last:
                if not self._shutdown.acquire(timeout=30):
                    raise SourceCleanupUnconfirmed("driver account retirement already pending")
                try:
                    with self._lock:
                        account, thread = self._account, self._thread
                    if account is not None:
                        account.stop()
                    join_thread(thread, timeout=6)
                    require_retired(thread)
                    if account is not None and not account.cleanup_confirmed:
                        raise SourceCleanupUnconfirmed("driver account cleanup unconfirmed")
                    with self._lock:
                        self._account = self._thread = None
                        self._retiring = False
                finally:
                    self._shutdown.release()

    @property
    def stopped(self) -> bool:
        with self._lock:
            return self._stopped

    def prepare(self, entries: tuple[P2PEnrollment, ...], *, motion_grants: tuple[PtzGrant, ...] = (),
                motion_valid: Callable[[str], bool] | None = None) -> NativeAccount:
        PtzAuthority(motion_grants)
        if motion_valid is not None and (not callable(motion_valid) or not motion_grants):
            raise ValueError("motion validator requires explicit reviewed grants")
        with self._lock:
            if (self._closed or self._retiring or not self._owner.acquired
                    or (self._managed and not self._intents)):
                raise SourceUnavailable("driver account service admission closed")
            if self._account is not None:
                alive = self._thread is not None and self._thread.is_alive()
                if alive or not self._account.cleanup_confirmed:
                    if not self._account.matches(entries) or not self._account.matches_motion(motion_grants):
                        raise ControlNotReady("another native account scope is still owned")
                    return self._account
            account = (NativeAccount(entries, motion_grants=motion_grants, motion_valid=motion_valid)
                       if motion_grants or motion_valid is not None else NativeAccount(entries))
            thread = threading.Thread(target=self._run, args=(account,),
                                      name="yoosee-native-account", daemon=True)
            self._account, self._thread = account, thread
            try:
                thread.start()
            except BaseException:
                account.stop()
                if thread.ident is None and not thread.is_alive():
                    self._account = self._thread = None
                else:
                    self._closed = True  # Unknown birth; retain both handles.
                raise
            return account

    def _source_cancelled(self) -> bool:
        with self._lock:
            return self._closed or (self._managed and not self._intents)

    def _run(self, account: NativeAccount) -> None:
        try:
            account.run(cancelled=self._source_cancelled)
        except BaseException:
            # The account retains SDK fencing/cleanup uncertainty. No retry,
            # raw exception logging or optimistic handle deletion in this thread.
            pass

    def binding(self, entry: P2PEnrollment) -> LeasedReadSession:
        with self._lock:
            account = self._account
            if self._closed or account is None or not account.includes(entry):
                raise ControlNotReady("native account has no matching enrolled camera")
        return account.binding(entry.camera_id or "")

    def stop(self) -> None:
        if not self._shutdown.acquire(timeout=30):
            raise SourceCleanupUnconfirmed("driver account shutdown already pending")
        try:
            with self._lock:
                self._closed = True
                account, thread = self._account, self._thread
            if account is not None:
                account.stop()
            join_thread(thread, timeout=30)
            require_retired(thread)
            if account is not None and not account.cleanup_confirmed:
                raise SourceCleanupUnconfirmed("driver account cleanup unconfirmed")
            with self._lock:
                self._stopped = True
                self._account = self._thread = None
        finally:
            self._shutdown.release()
