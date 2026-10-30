"""Preflight, one typed submission and bounded readback on a native owner."""

import logging
import time
from collections.abc import Callable

from ...db.p2p import P2PEnrollment
from ..contracts import ControlNotReady, ControlOperationError, ControlResult, ControlValue
from .p2p.model_control_inbox import ControlWriteTicket
from .shared_control_reads import read, session_for
from .shared_read_session import SharedReadSession

log = logging.getLogger(__name__)


def apply(entry: P2PEnrollment, key: str, value: ControlValue, raw: int,
          submit: Callable[[SharedReadSession], ControlWriteTicket]) -> ControlResult | None:
    if key not in {'speaker_volume', 'orientation', 'night_vision'}:
        raise ValueError('supported typed shared control required')
    session = session_for(entry)
    if session is None:
        return None
    previous = read(entry, key)
    if previous is None or previous.native_previous_value is None:
        raise ControlNotReady('native control preflight unavailable')
    if previous.native_previous_value == raw:
        return ControlResult(key=key, value=value, previous_value=previous.value,
            changed=False, verified=True, authenticated=True, error_code=0,
            native_previous_value=raw, native_requested_value=raw)
    # Do not silently change owner/generation between preflight and submission.
    if session_for(entry) is not session:
        raise ControlNotReady('native control owner changed before submission')
    try:
        ticket = submit(session)
    except ValueError:
        raise ControlNotReady('native session is not accepting control changes') from None
    result = None
    try:
        deadline = time.monotonic()+2.5
        while time.monotonic() < deadline:
            result = session.take_control_write(ticket)
            if result is not None:
                break
            time.sleep(0.005)
    finally:
        session.cancel_control_write(ticket)
    if result is None or result.status != 'response' or result.error_code != 0:
        log.warning('native control unconfirmed camera_id=%s control=%s status=%s error_code=%s transport_ack=%s',
            entry.camera_id, key, result.status if result else 'caller_timeout',
            result.error_code if result else None, result.transport_acknowledged if result else False)
        raise ControlOperationError('native control write unconfirmed; command was not repeated')
    if session_for(entry) is not session:
        raise ControlOperationError('native control owner changed; command was not repeated')
    # D3 acknowledges acceptance before this firmware updates the readable model.
    # Match the existing driver's bounded read-only settling checks. Never send
    # D2 again merely because the first readback still contains the old value.
    current = None
    verify_until = time.monotonic()+5
    for attempt in range(5):
        if attempt:
            time.sleep(0.5)
        if time.monotonic() >= verify_until or session_for(entry) is not session:
            break
        current = read(entry, key)
        if current is not None and current.native_previous_value == raw:
            break
    if current is None or current.native_previous_value != raw:
        log.warning('native control readback mismatch camera_id=%s control=%s', entry.camera_id, key)
        raise ControlOperationError('camera did not confirm the requested control value')
    return ControlResult(key=key, value=value, previous_value=previous.value,
        changed=True, verified=True, authenticated=True,
        transport_acknowledged=result.transport_acknowledged, application_acknowledged=True,
        error_code=0, native_previous_value=previous.native_previous_value, native_requested_value=raw)
