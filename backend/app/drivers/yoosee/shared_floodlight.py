"""One type-11 write plus mandatory type-12 readback on the existing native owner."""

import time

from ...db.p2p import P2PEnrollment
from ..contracts import ControlNotReady, ControlOperationError, ControlResult
from .p2p.lamp_read_job import LampReadResult
from .shared_control_reads import read, session_for
from .shared_read_session import SharedReadSession


def _exchange(session: SharedReadSession, enabled: bool) -> LampReadResult | None:
    try:
        ticket = session.submit_lamp_write(enabled)
    except ValueError:
        raise ControlNotReady('native lamp channel is not accepting changes') from None
    try:
        until = time.monotonic() + 2.5
        while time.monotonic() < until:
            result = session.take_lamp_write(ticket)
            if result is not None:
                return result
            time.sleep(0.005)
        return None
    finally:
        session.cancel_lamp_write(ticket)


def write(entry: P2PEnrollment, enabled: bool) -> ControlResult | None:
    if type(enabled) is not bool:
        raise ValueError('white-light state must be a boolean')
    session = session_for(entry)
    if session is None:
        return None
    previous = read(entry, 'white_light')
    if previous is None or type(previous.value) is not bool:
        raise ControlNotReady('native lamp preflight unavailable')
    if previous.value is enabled:
        return ControlResult(key='white_light', value=enabled, previous_value=previous.value,
            changed=False, verified=True, authenticated=True)
    if session_for(entry) is not session:
        raise ControlNotReady('native lamp owner changed before submission')
    result = _exchange(session, enabled)
    if result is None:
        raise ControlOperationError('native lamp change unconfirmed; command was not repeated')
    acknowledged = result.status == 'response' and result.error_code == 0
    # Some reviewed firmware omits type-11 replies. Only a send followed by a
    # timeout may be resolved by exact readback. Rejection/cancellation cannot.
    missing_reply = result.status == 'timeout' and result.sent
    if not acknowledged and not missing_reply:
        raise ControlOperationError('native lamp change rejected or unconfirmed; command was not repeated')
    until = time.monotonic() + 5
    for attempt in range(5):
        if attempt:
            time.sleep(0.4)
        if session_for(entry) is not session or time.monotonic() >= until:
            break
        current = read(entry, 'white_light')
        if current is not None and current.value is enabled:
            return ControlResult(key='white_light', value=enabled, previous_value=previous.value,
                changed=True, verified=True, authenticated=True,
                transport_acknowledged=result.transport_acknowledged,
                application_acknowledged=acknowledged, error_code=0 if acknowledged else None)
    raise ControlOperationError('camera did not confirm the lamp state; command was not repeated')
