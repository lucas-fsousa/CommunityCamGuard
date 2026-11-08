"""One explicit account owner, several camera leases; no thread or auto-login."""

import sqlite3
import threading
import time
from collections.abc import Callable
from contextlib import ExitStack

from ...camera_identity import valid_camera_id
from ...db import p2p
from ...db.errors import is_lock_busy
from ...db.p2p import P2PEnrollment
from ..contracts import ControlNotReady
from .leased_read_session import LeasedReadSession
from .native_ptz import reserve_read_handoff
from .p2p.account_policy import AccountPolicy
from .p2p.account_read_connect import ReadConnectFailure, open_read_connection
from .p2p.account_read_lifecycle import AccountReadLifecycle, ReadConnection
from .p2p.contracts import OnlineDevice, P2PProbeError
from .p2p.control_ownership import (
    ControlChannelBusy,
    block_uncertain_channel,
    control_channel_contended,
)
from .p2p.media_lease_inbox import MediaLeaseInbox
from .p2p.ptz_authority import PtzAuthority, PtzGrant
from .shared_control_reads import reserve_native_group
from .shared_read_session import SharedReadResult


class NativeBindingUnavailable(ControlNotReady):
    """Account is no longer accepting bindings; no camera lease was admitted."""


class NativeAccount:
    """Caller owns run() and SDK exclusion through confirmed account retirement.

    Account ID equality is not media authorization: each binding must also match
    an enrolled identity and a target in this login's authenticated inventory.
    Other members' access tokens are never used to create additional logins.
    """

    def __init__(self, entries: tuple[P2PEnrollment, ...], *,
                 current_enrollment: Callable[[str], P2PEnrollment | None] = p2p.read_registered_enrollment,
                 motion_grants: tuple[PtzGrant, ...] = (), motion_valid: Callable[[str], bool] | None = None,
                 clock: Callable[[], float] = time.monotonic):
        if (not isinstance(entries, tuple) or not entries
                or any(not isinstance(entry, P2PEnrollment) or not entry.camera_id
                    or not valid_camera_id(entry.camera_id) or type(entry.device_id) is not str
                    or not entry.device_id.isascii() or not entry.device_id.isdecimal()
                    or len(entry.device_id) > 20 or not 0 < int(entry.device_id) < 1 << 64
                    or str(int(entry.device_id)) != entry.device_id
                    or type(entry.access_id) is not int or not 0 <= entry.access_id < 1 << 64
                    for entry in entries)
                or len({entry.camera_id for entry in entries}) != len(entries)
                or len({entry.device_id for entry in entries}) != len(entries)
                or len({entry.access_id for entry in entries}) != 1):
            raise ValueError("bounded enrolled single-account group required")
        PtzAuthority(motion_grants)
        if (any(not any(grant.access_id == entry.access_id and str(grant.device_id) == entry.device_id
                        for entry in entries) for grant in motion_grants)
                or (motion_valid is not None and (not callable(motion_valid) or not motion_grants))):
            raise ValueError("motion scope must belong to enrolled account members")
        self._entries, self._current = entries, current_enrollment
        self._motion_grants, self._motion_valid = motion_grants, motion_valid
        self._clock = clock
        self._lifecycle = AccountReadLifecycle(policy=AccountPolicy.CONTINUOUS, state_reads=True,
                                             motion_grants=motion_grants, clock=clock)
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._started = False
        self._inbox: MediaLeaseInbox | None = None
        self._cleanup_confirmed = False
        self._result_reason = "preparing"
        self._connection_failure = ""
        self._targets: tuple[OnlineDevice, ...] = ()

    def matches(self, entries: tuple[P2PEnrollment, ...]) -> bool:
        return self._entries == entries

    def includes(self, entry: P2PEnrollment) -> bool:
        return entry in self._entries

    def matches_motion(self, grants: tuple[PtzGrant, ...]) -> bool:
        return self._motion_grants == grants

    @property
    def cleanup_confirmed(self) -> bool:
        with self._lock:
            return self._cleanup_confirmed

    @property
    def result_reason(self) -> str:
        with self._lock:
            return self._result_reason

    @property
    def connection_failure(self) -> str:
        with self._lock:
            return self._connection_failure

    @property
    def ready(self) -> bool:
        with self._lock:
            inbox = self._inbox
        return inbox is not None and self._lifecycle.owns_media_leases(inbox)

    def stop(self) -> None:
        self._stop.set()
        self._lifecycle.stop()

    def binding(self, camera_id: str) -> LeasedReadSession:
        with self._lock:
            inbox = self._inbox
            targets = self._targets
        entry = next((entry for entry in self._entries if entry.camera_id == camera_id), None)
        if entry is None or self._current(camera_id) != entry:
            raise ControlNotReady("shared native account is not ready for this enrollment")
        if inbox is None or not self._lifecycle.owns_media_leases(inbox):
            raise NativeBindingUnavailable("shared native account is not accepting bindings")
        # Only exact records retained by the authenticated connection can bind.
        target = next((target for target in targets if str(target.device_id) == entry.device_id), None)
        if target is None or not target.status:
            raise ControlNotReady("shared native account target is not online")
        grants = tuple(grant for grant in self._motion_grants if grant.device_id == target.device_id)
        validator = self._motion_valid
        return LeasedReadSession(entry, target, self._lifecycle, inbox,
            current_enrollment=lambda: self._current(camera_id), motion_grants=grants,
            motion_valid=(lambda: validator(camera_id)) if validator is not None and grants else None)

    def _confirmed(self, result: SharedReadResult) -> SharedReadResult:
        with self._lock:
            self._cleanup_confirmed = True
            self._result_reason = result.reason
        return result

    def run(self, *, cancelled: Callable[[], bool]) -> SharedReadResult:
        with self._lock:
            if self._started:
                raise ValueError("native account already started")
            self._started = True
        if self._stop.is_set() or cancelled():
            return self._confirmed(SharedReadResult("stopped"))
        with ExitStack() as ownership:
            try:
                ownership.enter_context(reserve_native_group(tuple(entry.camera_id or "" for entry in self._entries)))
                ownership.enter_context(reserve_read_handoff(wait=False))
            except (ControlNotReady, ControlChannelBusy):
                # No socket transfer/authentication took place. Normal exit also
                # releases any logical group acquired before the SDK was busy.
                result = SharedReadResult("busy")
            else:
                result = self._run_owned(cancelled)
        # Scope exit may itself fail; do not publish a cleanup receipt earlier.
        return self._confirmed(result)

    def _run_owned(self, cancelled: Callable[[], bool]) -> SharedReadResult:
        root = self._entries[0]
        next_check = 0.0
        changed = False
        registry_busy = False
        def should_stop() -> bool:
            nonlocal next_check, changed, registry_busy
            if self._stop.is_set() or cancelled() or control_channel_contended() or changed or registry_busy:
                return True
            if self._clock() >= next_check:
                next_check = self._clock() + 1
                try:
                    changed = self._current(root.camera_id or "") != root
                except sqlite3.OperationalError as error:
                    if not is_lock_busy(error):
                        raise
                    registry_busy = True  # Retire, never assume stale credentials are valid.
            return changed or registry_busy
        if should_stop():
            return SharedReadResult("busy" if registry_busy else "stopped")

        def connect(_: int) -> ReadConnection:
            return open_read_connection(root, camera_id=root.camera_id or "", device_id=root.device_id,
                additional_device_ids=tuple(entry.device_id for entry in self._entries[1:]),
                cancelled=should_stop)

        def leases(connection: ReadConnection, generation: int) -> MediaLeaseInbox:
            if connection.access_id != root.access_id:
                raise ValueError("authenticated native account identity mismatch")
            inbox = MediaLeaseInbox(generation, root.access_id,
                                    targets=connection.targets, capacity=len(self._entries))
            with self._lock:
                self._targets = connection.targets
                self._inbox = inbox
            return inbox

        try:
            result = self._lifecycle.run_media_leases(connect, leases=leases, cancelled=should_stop)
        except ReadConnectFailure as failure:
            with self._lock:
                self._connection_failure = failure.reason
            # This exception certifies physical socket cleanup, unlike OSError.
            return SharedReadResult("busy" if registry_busy else "connection")
        except BaseException:
            block_uncertain_channel()
            raise
        if self._lifecycle.state != "idle":
            block_uncertain_channel()
            raise P2PProbeError("shared native account cleanup unconfirmed")
        return SharedReadResult("busy" if registry_busy else
                                "account_ended" if result.media_account_retired else result.reason, result)
