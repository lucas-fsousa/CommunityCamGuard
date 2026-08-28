"""One-shot renewal of expired P2P access material from the encrypted vendor account."""

from __future__ import annotations

import threading
from collections.abc import Callable
from typing import TypeVar

from ..db import p2p, vendor_account
from ..db.p2p import P2PEnrollment
from .account import VendorAccountError, refresh_account_session
from .client import InitInfoRejectedError, P2PProbeError

ResultT = TypeVar("ResultT")
_refresh_lock = threading.Lock()


def run_with_fresh_access(
    enrollment: P2PEnrollment,
    operation: Callable[[P2PEnrollment], ResultT],
) -> ResultT:
    """Run once, renew only on the explicit stale-session code, then retry exactly once."""

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
            stored = vendor_account.get_account()
            if stored is None:
                raise P2PProbeError(
                    "P2P session expired and no renewable vendor account is configured"
                )
            try:
                session = refresh_account_session(stored.session)
                vendor_account.update_session(session)
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
