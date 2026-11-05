"""Silent resource enumeration through the active native account, never login."""

import time

from ...db.p2p import P2PEnrollment
from ..contracts import ControlNotReady, ControlOperationError
from .p2p.alarm_voice import AlarmVoiceCatalog
from .p2p.alarm_voice_catalog import P2PAlarmVoiceCatalog
from .shared_control_reads import session_for
from .shared_read_session import SharedReadSession


def _part(session: SharedReadSession, system: bool) -> tuple[AlarmVoiceCatalog, bool]:
    try:
        ticket = session.submit_catalog_read(system)
    except ValueError:
        raise ControlNotReady('native account is not accepting catalogue reads') from None
    try:
        deadline = time.monotonic()+2.5
        while time.monotonic() < deadline:
            result = session.take_catalog_read(ticket)
            if result is not None:
                if result.status != 'response' or result.catalog is None:
                    raise ControlOperationError('native resource catalogue was not confirmed')
                return result.catalog, result.transport_acknowledged
            time.sleep(0.005)
        raise ControlOperationError('native resource catalogue timed out')
    finally:
        session.cancel_catalog_read(ticket)


def read(entry: P2PEnrollment) -> P2PAlarmVoiceCatalog | None:
    session = session_for(entry)
    if session is None:
        return None
    system, system_ack = _part(session, True)
    if session_for(entry) is not session:
        raise ControlNotReady('native resource catalogue owner changed')
    custom, custom_ack = _part(session, False)
    if session_for(entry) is not session:
        raise ControlNotReady('native resource catalogue owner changed')
    resources = system.resources+custom.resources
    if len({resource.key for resource in resources}) != len(resources):
        raise ControlOperationError('native resource catalogue contains conflicting options')
    return P2PAlarmVoiceCatalog(entry.device_id, system.reported_total, custom.reported_total,
                                system_ack and custom_ack, resources)
