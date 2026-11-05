"""Bounded pulse on the native owner, with owned OFF drain even if its caller leaves."""

import time

from ...db.p2p import P2PEnrollment
from ..contracts import ControlNotReady, ControlOperationError, ControlResult
from .p2p.siren import SIREN_READ_PATH
from .shared_control_reads import session_for
from .shared_read_session import SharedReadSession
from .siren_evidence import siren_state


def _state(session: SharedReadSession) -> int:
    try:
        ticket = session.submit(SIREN_READ_PATH)
    except ValueError:
        raise ControlNotReady('native account is not accepting siren state reads') from None
    try:
        deadline = time.monotonic()+2.5
        while time.monotonic() < deadline:
            result = session.take(ticket)
            if result is not None:
                reply = result.completion
                state = (siren_state(reply.value) if result.status == 'response' and reply is not None
                         and reply.error_code == 0 else None)
                if state is None:
                    raise ControlOperationError('native siren state was not confirmed')
                return state
            time.sleep(0.005)
        raise ControlOperationError('native siren state timed out')
    finally:
        session.cancel(ticket)


def pulse(entry: P2PEnrollment, seconds: int) -> ControlResult | None:
    if type(seconds) is not int or seconds not in (2, 5, 10):
        raise ControlOperationError('siren pulse must be 2, 5 or 10 seconds')
    session = session_for(entry)
    if session is None:
        return None
    if _state(session) != 1:
        raise ControlNotReady('siren is already active; no pulse was started')
    if session_for(entry) is not session:
        raise ControlNotReady('native siren owner changed before submission')
    try:
        ticket = session.submit_siren_pulse(seconds)
    except ValueError:
        raise ControlNotReady('native account is not accepting siren pulses') from None
    result = None
    try:
        deadline = time.monotonic()+seconds+5
        while time.monotonic() < deadline:
            result = session.take_siren_pulse(ticket)
            if result is not None:
                break
            time.sleep(0.005)
    finally:
        # Cancellation cannot discard a claimed ON; the sole owner drains OFF.
        session.cancel_siren_pulse(ticket)
    if (result is None or result.status != 'completed' or not result.start_attempted
            or not result.off_confirmed or result.on_error != 0 or result.off_error != 0):
        raise ControlOperationError('native siren pulse unconfirmed; ON was not repeated')
    if session_for(entry) is not session or _state(session) != 1 or session_for(entry) is not session:
        raise ControlOperationError('native siren final OFF state was not confirmed')
    return ControlResult(key='siren_pulse', value=seconds, changed=True, verified=True,
        authenticated=True, transport_acknowledged=result.transport_acknowledged,
        application_acknowledged=True, error_code=0)
