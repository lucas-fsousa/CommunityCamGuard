"""Server-owned, lazy account worker. One SDK owner, no per-camera login thread."""

import threading

from ...db.p2p import P2PEnrollment
from ...media.server_owner import ServerOwner
from ...media.source_registry import SourceCleanupUnconfirmed, SourceUnavailable
from ...services.thread_shutdown import join_thread, require_retired
from ..contracts import ControlNotReady
from .leased_read_session import LeasedReadSession
from .native_account import NativeAccount


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

    @property
    def stopped(self) -> bool:
        with self._lock:
            return self._stopped

    def prepare(self, entries: tuple[P2PEnrollment, ...]) -> NativeAccount:
        with self._lock:
            if self._closed or not self._owner.acquired:
                raise SourceUnavailable("driver account service admission closed")
            if self._account is not None:
                alive = self._thread is not None and self._thread.is_alive()
                if alive or not self._account.cleanup_confirmed:
                    if not self._account.matches(entries):
                        raise ControlNotReady("another native account scope is still owned")
                    return self._account
            account = NativeAccount(entries)
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

    @staticmethod
    def _run(account: NativeAccount) -> None:
        try:
            account.run(cancelled=lambda: False)
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
