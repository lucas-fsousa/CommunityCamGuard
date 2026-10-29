"""Verified volume changes through the already authenticated native owner."""

import logging
import time

from ...db.p2p import P2PEnrollment
from ..contracts import ControlNotReady, ControlOperationError, ControlResult
from .p2p.volume import VOLUME_RAW_BY_PERCENT
from .shared_control_reads import read, session_for

log = logging.getLogger(__name__)


def write(entry: P2PEnrollment, percent: int) -> ControlResult | None:
    if type(percent) is not int or percent not in VOLUME_RAW_BY_PERCENT:
        raise ValueError('speaker volume must be 0, 25, 50, 75 or 100 percent')
    session = session_for(entry)
    if session is None:
        return None
    previous = read(entry, 'speaker_volume')
    if previous is None or previous.native_previous_value is None:
        raise ControlNotReady('native volume preflight unavailable')
    raw = VOLUME_RAW_BY_PERCENT[percent]
    if previous.native_previous_value == raw:
        return ControlResult(key='speaker_volume', value=percent, previous_value=previous.value,
            changed=False, verified=True, authenticated=True, error_code=0,
            native_previous_value=raw, native_requested_value=raw)
    # Do not silently change owner/generation between preflight and submission.
    if session_for(entry) is not session:
        raise ControlNotReady('native volume owner changed before submission')
    try:
        ticket = session.submit_volume_write(percent)
    except ValueError:
        raise ControlNotReady('native session is not accepting volume changes') from None
    result = None
    try:
        deadline = time.monotonic()+2.5
        while time.monotonic() < deadline:
            result = session.take_volume_write(ticket)
            if result is not None:
                break
            time.sleep(0.005)
    finally:
        session.cancel_volume_write(ticket)
    if result is None or result.status != 'response' or result.error_code != 0:
        log.warning('native volume unconfirmed camera_id=%s status=%s error_code=%s transport_ack=%s',
            entry.camera_id, result.status if result else 'caller_timeout',
            result.error_code if result else None, result.transport_acknowledged if result else False)
        raise ControlOperationError('native volume write unconfirmed; command was not repeated')
    if session_for(entry) is not session:
        raise ControlOperationError('native volume owner changed; command was not repeated')
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
        current = read(entry, 'speaker_volume')
        if current is not None and current.native_previous_value == raw:
            break
    if current is None or current.native_previous_value != raw:
        log.warning('native volume readback mismatch camera_id=%s', entry.camera_id)
        raise ControlOperationError('camera did not confirm the requested speaker volume')
    return ControlResult(key='speaker_volume', value=percent, previous_value=previous.value,
        changed=True, verified=True, authenticated=True,
        transport_acknowledged=result.transport_acknowledged, application_acknowledged=True,
        error_code=0, native_previous_value=previous.native_previous_value, native_requested_value=raw)
