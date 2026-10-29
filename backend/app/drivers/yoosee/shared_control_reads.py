"""Driver-only state reads on the native source's already-owned account/socket."""

import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager

from ...db.p2p import P2PEnrollment
from ..contracts import ControlNotReady, ControlOperationError, ControlResult, ControlValue
from .p2p.orientation import ORIENTATION_READ_PATH, ORIENTATION_VALUES, extract_orientation
from .p2p.smart_protection import SMART_PROTECTION_READ_PATH, extract_smart_protection_enabled
from .p2p.smart_protection_schedule import extract_smart_protection_schedule
from .p2p.volume import VOLUME_READ_PATH, extract_volume_raw, volume_percent
from .shared_read_session import SharedReadSession

_lock = threading.Lock()
_sessions: dict[str, SharedReadSession] = {}
_owners: dict[str, object] = {}  # retain control admission across clean reconnect gaps
_paths = {"speaker_volume": VOLUME_READ_PATH, "smart_protection": SMART_PROTECTION_READ_PATH,
          "smart_protection_schedule": SMART_PROTECTION_READ_PATH, "orientation": ORIENTATION_READ_PATH}


def has_session(camera_id: str | None) -> bool:
    with _lock:
        return camera_id in _sessions or camera_id in _owners


def session_for(entry: P2PEnrollment) -> SharedReadSession | None:
    if not entry.camera_id:
        return None
    with _lock:
        session = _sessions.get(entry.camera_id)
        recovering = entry.camera_id in _owners and session is None
    if recovering:
        raise ControlNotReady("native media session is recovering; control was not repeated")
    if session is not None and not session.matches_enrollment(entry):
        raise ControlNotReady("native shared-control session is not ready for this enrollment")
    return session


def require_legacy_available(camera_id: str | None) -> None:
    """Unintegrated operations must not displace a live native account to log in again."""
    with _lock:
        occupied = camera_id in _sessions or camera_id in _owners
    if occupied:
        raise ControlNotReady("this operation cannot yet share the active native streaming session")


@contextmanager
def reserve_native_owner(camera_id: str) -> Iterator[None]:
    """One recovery-run lease, not a broker lock. Unknown exit stays reserved."""
    owner = object()
    with _lock:
        if _owners or _sessions:
            raise ControlNotReady("native control owner already reserved")
        _owners[camera_id] = owner
    confirmed = False
    try:
        yield
        confirmed = True
    finally:
        with _lock:
            if confirmed and _owners.get(camera_id) is owner:
                del _owners[camera_id]


@contextmanager
def publish(camera_id: str, session: SharedReadSession) -> Iterator[None]:
    """Run-lifetime lease, not a second owner, thread, socket or capability grant."""
    with _lock:
        if _sessions or (_owners and camera_id not in _owners):
            raise ControlNotReady("native shared-control owner already published")
        _sessions[camera_id] = session
    try:
        yield
    finally:
        with _lock:
            if _sessions.get(camera_id) is session:
                del _sessions[camera_id]


def read(entry: P2PEnrollment, key: str) -> ControlResult | None:
    """None only when no native owner exists; active-owner failures NEVER fall back/login."""
    if not entry.camera_id:
        return None
    if key not in _paths and key != "white_light":
        raise ControlOperationError("control is not a supported shared state read")
    session = session_for(entry)
    if session is None:
        return None
    if key == "white_light":
        return _read_lamp(session)
    try:
        ticket = session.submit(_paths[key])
    except ValueError:
        raise ControlNotReady("native shared-control session is not accepting reads") from None
    try:
        deadline = time.monotonic() + 2.5
        while time.monotonic() < deadline:
            outcome = session.take(ticket)
            if outcome is not None:
                completion = outcome.completion
                if outcome.status != "response" or completion is None or completion.error_code != 0:
                    raise ControlOperationError("native state read was not confirmed")
                raw = None
                value: ControlValue | None
                if key == "speaker_volume":
                    raw = extract_volume_raw(completion.value)
                    value = volume_percent(raw) if raw is not None else None
                elif key == 'orientation':
                    raw = extract_orientation(completion.value)
                    value = next((name for name, number in ORIENTATION_VALUES.items() if number == raw), None)
                elif key == "smart_protection":
                    value = extract_smart_protection_enabled(completion.value)
                else:
                    value = extract_smart_protection_schedule(completion.value)
                if value is None:
                    raise ControlOperationError("native state read returned an unsupported state")
                return ControlResult(key=key, value=value,
                    verified=True, authenticated=True, direct_connection=False,
                    transport_acknowledged=completion.transport_result == 0,
                    application_acknowledged=True, error_code=0, native_previous_value=raw)
            time.sleep(0.005)
        raise ControlOperationError("native state read timed out")
    finally:
        session.cancel(ticket)


def _read_lamp(session: SharedReadSession) -> ControlResult:
    try:
        ticket = session.submit_lamp_read()
    except ValueError:
        raise ControlNotReady("native account is not accepting lamp-state reads") from None
    try:
        deadline = time.monotonic() + 2.5
        while time.monotonic() < deadline:
            result = session.take_lamp_read(ticket)
            if result is not None:
                if result.status != "response" or type(result.enabled) is not bool:
                    raise ControlOperationError("native lamp-state read was not confirmed")
                return ControlResult(key="white_light", value=result.enabled, verified=True,
                    authenticated=True, direct_connection=False,
                    transport_acknowledged=result.transport_acknowledged,
                    application_acknowledged=True)
            time.sleep(0.005)
        raise ControlOperationError("native lamp-state read timed out")
    finally:
        session.cancel_lamp_read(ticket)
