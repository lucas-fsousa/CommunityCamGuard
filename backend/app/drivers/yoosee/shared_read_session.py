"""On-demand continuous read session under the server's legacy exclusion boundary.

Not a periodic warmup, public endpoint or production media source. The server
supplies its shutdown predicate and owns the calling thread. No actions admitted.
"""

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass

from ...db import p2p
from ..contracts import ControlNotReady
from .native_ptz import reserve_read_handoff
from .p2p.account_policy import AccountPolicy
from .p2p.account_read_connect import ReadConnectFailure, open_read_connection
from .p2p.account_read_lifecycle import AccountReadLifecycle
from .p2p.account_read_worker import WorkerSummary
from .p2p.broker_mailbox import MailboxTicket, ReadOutcome
from .p2p.contracts import P2PProbeError
from .p2p.control_ownership import ControlChannelBusy, control_channel_contended


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
                 clock: Callable[[], float] = time.monotonic):
        if (not camera_id or not device_id or len(device_id) > 20
                or not device_id.isascii() or not device_id.isdecimal()
                or not 0 < int(device_id) < 1 << 64):
            raise ValueError("shared read target required")
        self._camera_id, self._device_id, self._clock = camera_id, device_id, clock
        self._lifecycle = AccountReadLifecycle(clock=clock, policy=AccountPolicy.CONTINUOUS)
        self._stop = threading.Event()
        self._claim = threading.Lock()
        self._started = False

    def stop(self) -> None:
        self._stop.set()
        self._lifecycle.stop()

    def submit(self, path: str) -> MailboxTicket:
        """Only the worker's existing identity-read allowlist; no caller device ID."""
        return self._lifecycle.submit(int(self._device_id), path)

    def take(self, ticket: MailboxTicket) -> ReadOutcome | None:
        return self._lifecycle.take(ticket)

    def run(self, *, cancelled: Callable[[], bool]) -> SharedReadResult:
        with self._claim:
            if self._started:
                raise ValueError("shared read session already started")
            self._started = True
        if self._stop.is_set() or cancelled():
            return SharedReadResult("stopped")
        try:
            with reserve_read_handoff(wait=False):
                return self._run_owned(cancelled)
        except (ControlChannelBusy, ControlNotReady):
            return SharedReadResult("busy")

    def _run_owned(self, cancelled: Callable[[], bool]) -> SharedReadResult:
        try:
            entry = p2p.read_registered_enrollment(self._camera_id)
        except Exception:
            return SharedReadResult("registry_unavailable")
        if (entry is None or entry.camera_id != self._camera_id
                or entry.device_id != self._device_id):
            return SharedReadResult("enrollment_changed")
        reason: str | None = None
        next_check = self._clock()  # Recheck immediately before opening.

        def should_yield() -> bool:
            nonlocal reason, next_check
            if reason is not None:
                return True
            if self._stop.is_set() or cancelled():
                reason = "stopped"
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
        try:
            result = self._lifecycle.run(lambda _: open_read_connection(entry,
                camera_id=self._camera_id, device_id=self._device_id,
                cancelled=should_yield), cancelled=should_yield)
        except ReadConnectFailure:
            return SharedReadResult(reason or "connection")
        if result.socket_close_failed:
            raise P2PProbeError("shared read socket cleanup unconfirmed")
        return SharedReadResult(reason or result.reason, result)
