"""Driver-only state reads on the native source's already-owned account/socket."""

import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager

from ...db.p2p import P2PEnrollment
from ..contracts import ControlNotReady, ControlOperationError, ControlResult
from .p2p.volume import VOLUME_READ_PATH, extract_volume_raw, volume_percent
from .shared_read_session import SharedReadSession

_lock = threading.Lock()
_sessions: dict[str, SharedReadSession] = {}


@contextmanager
def publish(camera_id: str, session: SharedReadSession) -> Iterator[None]:
    """Run-lifetime lease, not a second owner, thread, socket or capability grant."""
    with _lock:
        if _sessions:
            raise ControlNotReady("native shared-control owner already published")
        _sessions[camera_id] = session
    try:
        yield
    finally:
        with _lock:
            if _sessions.get(camera_id) is session:
                del _sessions[camera_id]


def read_volume(entry: P2PEnrollment) -> ControlResult | None:
    """None only when no native owner exists; active-owner failures NEVER fall back/login."""
    if not entry.camera_id:
        return None
    with _lock:
        session = _sessions.get(entry.camera_id)
    if session is None:
        return None
    if not session.matches_enrollment(entry):
        raise ControlNotReady("native shared-control session is not ready for this enrollment")
    try:
        ticket = session.submit(VOLUME_READ_PATH)
    except ValueError:
        raise ControlNotReady("native shared-control session is not accepting reads") from None
    try:
        deadline = time.monotonic() + 2.5
        while time.monotonic() < deadline:
            outcome = session.take(ticket)
            if outcome is not None:
                completion = outcome.completion
                if outcome.status != "response" or completion is None or completion.error_code != 0:
                    raise ControlOperationError("native volume read was not confirmed")
                raw = extract_volume_raw(completion.value)
                if raw is None:
                    raise ControlOperationError("native volume read returned an unsupported state")
                return ControlResult(key="speaker_volume", value=volume_percent(raw),
                    verified=True, authenticated=True, direct_connection=False,
                    transport_acknowledged=completion.transport_result == 0,
                    application_acknowledged=True, error_code=0, native_previous_value=raw)
            time.sleep(0.005)
        raise ControlOperationError("native volume read timed out")
    finally:
        session.cancel(ticket)
