"""Operator-only silent catalogue review on the running native account, no grants."""

from ..db import p2p, registry
from ..drivers.contracts import ControlNotReady
from ..drivers.yoosee.capability_resources import resource_identity_digest
from ..drivers.yoosee.shared_alarm_catalog import read
from ..drivers.yoosee.shared_alarm_selection import _state
from ..drivers.yoosee.shared_control_reads import session_for
from ..services.camera_controls import CameraNotFound, _exclusive


def review_native_catalog(*, camera_id: str, device_id: str) -> dict[str, object]:
    """Trusted server-side target; no standalone fallback, write or proof renewal."""
    with _exclusive(camera_id):
        if registry.get_camera_by_id(camera_id) is None:
            raise CameraNotFound('camera not found')
        entry = p2p.get_enrollment_for_camera(camera_id)
        if entry is None or entry.camera_id != camera_id or entry.device_id != device_id:
            raise ValueError('native catalogue target enrollment mismatch')
        session = session_for(entry)
        if session is None:
            raise ControlNotReady('native catalogue review requires a running owner')
        result = read(entry)
        if result is None or session_for(entry) is not session:
            raise ControlNotReady('native catalogue review owner changed')
        current = _state(session)
        if session_for(entry) is not session:
            raise ControlNotReady('native catalogue review owner changed')
        selected = next((resource.key for resource in result.resources
                         if resource.resource_id == current.resource_id), None)
        return {'system_total':result.system_total, 'custom_total':result.custom_total,
                'selected_key':selected,
                'resources':[{**resource.public(), 'identity_digest':resource_identity_digest(resource)}
                             for resource in result.resources]}
