"""Select one freshly resolved alarm resource on the native owner; never play it."""

import time

from ...db.p2p import P2PEnrollment
from ..contracts import ControlNotReady, ControlOperationError, ControlResult
from . import alarm_resource_controls
from .capability_resources import ResourceObservation, certified_resources
from .p2p.alarm_voice import AlarmVoiceResource, parse_alarm_voice_option_key
from .p2p.alarm_voice_selection import (
    ALARM_VOICE_READ_PATH,
    AlarmVoiceSelectionState,
    extract_alarm_voice_selection,
)
from .p2p.contracts import P2PProbeError
from .shared_alarm_catalog import read as read_catalog
from .shared_control_reads import session_for
from .shared_device_controls import SharedDeviceControls


def _state(session: SharedDeviceControls) -> AlarmVoiceSelectionState:
    try:
        ticket = session.submit(ALARM_VOICE_READ_PATH)
    except ValueError:
        raise ControlNotReady('native account is not accepting resource state reads') from None
    try:
        deadline = time.monotonic()+2.5
        while time.monotonic() < deadline:
            result = session.take(ticket)
            if result is not None:
                completed = result.completion
                state = (extract_alarm_voice_selection(completed.value, exact=True)
                         if result.status == 'response' and completed is not None and completed.error_code == 0 else None)
                if state is None:
                    raise ControlOperationError('native resource state was not confirmed')
                return state
            time.sleep(0.005)
        raise ControlOperationError('native resource state timed out')
    finally:
        session.cancel(ticket)


def _certified(entry: P2PEnrollment, resource: AlarmVoiceResource) -> None:
    profile = alarm_resource_controls._profile(entry)
    now = time.time()
    observation = ResourceObservation(profile.camera_id, profile.identity, now, now+5,
                                      (resource,), True, True)
    if not certified_resources(observation, profile, camera_id=profile.camera_id,
                               identity=profile.identity, now=now):
        raise ControlOperationError('resource selection proof changed before submission')


def write(entry: P2PEnrollment, key: str) -> ControlResult | None:
    if type(key) is not str or parse_alarm_voice_option_key(key) is None:
        raise ControlOperationError('alarm voice must be a semantic catalogue option')
    session = session_for(entry)
    if session is None:
        return None
    managed = alarm_resource_controls.managed(entry.camera_id or '')
    try:
        if managed:
            resources = alarm_resource_controls._fresh_resources(entry, native_only=True)
        else:
            catalog = read_catalog(entry)
            if catalog is None:
                raise ControlNotReady('native resource catalogue owner changed')
            resources = catalog.resources
        resource = next((item for item in resources if item.key == key), None)
        if resource is None:
            raise ControlOperationError('resource is missing, replaced or not certified for this camera')
        if session_for(entry) is not session:
            raise ControlNotReady('native resource owner changed before preflight')
        previous = _state(session)
        if session_for(entry) is not session:
            raise ControlNotReady('native resource owner changed before submission')
        if managed:
            _certified(entry, resource)  # Revocation during preflight takes effect.
    except P2PProbeError as exc:
        raise ControlOperationError(str(exc)) from None
    if previous.resource_id == resource.resource_id:
        return ControlResult(key='alarm_voice', value=key, changed=False, verified=True,
            authenticated=True, error_code=0, native_previous_value=previous.logical_number,
            native_requested_value=resource.logical_number)
    try:
        ticket = session.submit_alarm_voice_write(resource)
    except ValueError:
        raise ControlNotReady('native account is not accepting resource selection') from None
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
        raise ControlOperationError('native resource selection unconfirmed; command was not repeated')
    deadline = time.monotonic()+5
    for attempt in range(5):
        if attempt:
            time.sleep(0.5)
        if time.monotonic() >= deadline or session_for(entry) is not session:
            break
        current = _state(session)
        if current.resource_id == resource.resource_id and session_for(entry) is session:
            return ControlResult(key='alarm_voice', value=key, changed=True, verified=True,
                authenticated=True, transport_acknowledged=result.transport_acknowledged,
                application_acknowledged=True, error_code=0,
                native_previous_value=previous.logical_number, native_requested_value=resource.logical_number)
    raise ControlOperationError('camera did not confirm the exact resource selection; command was not repeated')
