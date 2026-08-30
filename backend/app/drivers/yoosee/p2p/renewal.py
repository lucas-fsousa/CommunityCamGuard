"""One-shot renewal of expired P2P access material from the encrypted vendor account."""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from typing import TypeVar

from ....db import p2p
from ....db.p2p import P2PEnrollment
from .. import account_store
from .account import VendorAccountError, refresh_account_session
from .contracts import InitInfoRejectedError, P2PProbeError

ResultT = TypeVar("ResultT")
_refresh_lock = threading.Lock()
_session_locks_guard = threading.Lock()
_session_locks: dict[str, threading.Lock] = {}
_session_finished: dict[str, float] = {}
# The field unit accepts three fresh routes in quick succession but commonly drops the fourth until
# the oldest rendezvous ages out. Five seconds spaces four operations across that observed ~30 s
# window once their normal 4-6 s handshakes are included. A persistent session pool can remove this
# conservative pacing later without weakening write safety.
_SESSION_SETTLE_SECONDS = 5.0


def _session_lock(device_id: str) -> threading.Lock:
    with _session_locks_guard:
        return _session_locks.setdefault(device_id, threading.Lock())


def _run_with_renewal(
    enrollment: P2PEnrollment,
    operation: Callable[[P2PEnrollment], ResultT],
) -> ResultT:
    """Run the operation and perform at most one explicit stale-access renewal."""

    try:
        return operation(enrollment)
    except InitInfoRejectedError as exc:
        if exc.error_code != 0x216B:
            raise

    with _refresh_lock:
        current = p2p.get_enrollment(enrollment.device_id)
        if current is None:
            raise P2PProbeError("P2P enrollment disappeared during session renewal")
        if current.access_id == enrollment.access_id and current.access_token == enrollment.access_token:
            stored = account_store.get_account()
            if stored is None:
                raise P2PProbeError(
                    "P2P session expired and no renewable vendor account is configured"
                )
            try:
                session = refresh_account_session(stored.session)
                account_store.update_session(session)
                current = p2p.upsert_enrollment(
                    current.device_id,
                    access_id=session.p2p_access_id,
                    access_token=session.access_token,
                    dev_token=current.dev_token,
                    camera_id=current.camera_id,
                )
            except (OSError, ValueError, VendorAccountError) as refresh_error:
                raise P2PProbeError("P2P session renewal failed") from refresh_error

    return operation(current)


def run_with_fresh_access(
    enrollment: P2PEnrollment,
    operation: Callable[[P2PEnrollment], ResultT],
) -> ResultT:
    """Serialize one device, let its prior route settle, and renew stale access at most once."""

    device_id = enrollment.device_id
    with _session_lock(device_id):
        last_finished = _session_finished.get(device_id)
        if last_finished is not None:
            remaining = _SESSION_SETTLE_SECONDS - (time.monotonic() - last_finished)
            if remaining > 0:
                time.sleep(remaining)
        try:
            return _run_with_renewal(enrollment, operation)
        finally:
            _session_finished[device_id] = time.monotonic()
