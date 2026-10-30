"""Read-only capability refresh on the current native account; never log in again."""

import math
import time

from ...db import p2p
from ...db.p2p import P2PEnrollment
from . import capability_snapshot_store as store
from .capability_evidence import CAPABILITY_PATHS
from .capability_snapshot import CapabilitySnapshot, normalize_snapshot
from .p2p.contracts import P2PPropertyRead
from .p2p.white_light import P2PWhiteLightState
from .shared_control_reads import session_for
from .shared_read_session import SharedReadSession


def _current(session: SharedReadSession, entry: P2PEnrollment) -> bool:
    return (p2p.read_registered_enrollment(entry.camera_id or '') == entry
            and session_for(entry) is session and session.matches_enrollment(entry))


def collect(session: SharedReadSession, entry: P2PEnrollment, *, include_siren: bool) -> CapabilitySnapshot | None:
    """Sequential fixed B7 roots plus optional type-12 lamp STATUS, <=20 seconds.

    Every waiter is consumed/cancelled, and the same account/enrollment must survive
    the entire batch. No generic path, write, action, authentication or fallback.
    """
    deadline = time.monotonic() + 20
    reads: list[P2PPropertyRead | P2PWhiteLightState] = []
    for path in CAPABILITY_PATHS + (('Action.expelCtrl',) if include_siren else ()):
        if not _current(session, entry) or time.monotonic() >= deadline:
            return None
        ticket = session.submit(path)
        try:
            outcome = None
            while time.monotonic() < deadline and _current(session, entry):
                outcome = session.take(ticket)
                if outcome is not None:
                    break
                time.sleep(0.01)
            completion = outcome.completion if outcome is not None else None
            if (outcome is None or outcome.status != 'response' or completion is None
                    or completion.status != 'response' or completion.error_code != 0
                    or completion.request.path != path
                    or completion.request.device_id != int(entry.device_id)):
                return None
            reads.append(P2PPropertyRead(entry.device_id, path, True, False,
                completion.transport_result == 0, completion.error_code, completion.value))
        finally:
            session.cancel(ticket)
            session.take(ticket)  # consume a cancellation even if the batch lost its owner
    if time.monotonic() < deadline and _current(session, entry):
        ticket_lamp = session.submit_lamp_read()
        try:
            while time.monotonic() < deadline and _current(session, entry):
                light = session.take_lamp_read(ticket_lamp)
                if light is not None:
                    if light.status == 'response' and type(light.enabled) is bool:
                        reads.append(P2PWhiteLightState(entry.device_id, light.enabled,
                            True, False, light.transport_acknowledged, False))
                    break
                time.sleep(0.01)
        finally:
            session.cancel_lamp_read(ticket_lamp)
    if not _current(session, entry):
        return None
    return normalize_snapshot(tuple(reads), camera_id=entry.camera_id or '',
                              device_id=entry.device_id, collected_at=time.time())


def refresh(entry: P2PEnrollment, *, validity_seconds: float, include_siren: bool = False) -> bool:
    if (type(validity_seconds) not in (int, float) or not math.isfinite(validity_seconds)
            or not 0 < validity_seconds <= 86400):
        raise ValueError('snapshot validity must be positive and at most one day')
    session = session_for(entry)
    generation = store.refresh_generation(entry.camera_id or '')
    if session is None or generation is None:
        return False
    snapshot = collect(session, entry, include_siren=include_siren)
    if snapshot is None or not _current(session, entry):
        return False
    return store.replace_current(snapshot, generation=generation,
                                 expires_at=snapshot.collected_at + validity_seconds)
