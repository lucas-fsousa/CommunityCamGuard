"""Internal experimental Yoosee AV invocation, never exposed as a public control.

Must run INSIDE the server process: a separate CLI would not share its operation
locks. The trusted operator supplies the reviewed test camera pair (camera 3).
No environment defaults, browser-supplied target, automatic worker or retry.
"""

from collections.abc import Callable

from ..db import p2p, registry
from ..drivers.yoosee.p2p.av_route import AvRouteResult, probe_av_route
from ..services.camera_controls import CameraNotFound, _exclusive


def run_reviewed_native_av(*, camera_id: str, reviewed_camera_id: str,
                           reviewed_device_id: str,
                           cancelled: Callable[[], bool] = lambda: False) -> AvRouteResult:
    """One fixed three-second sample under the existing PTZ/audio/control lock.

    The two reviewed IDs must come from trusted internal configuration, not HTTP.
    This does not lock RTSP, other processes or background capability probes.
    """
    if not reviewed_camera_id or camera_id != reviewed_camera_id or not reviewed_device_id:
        raise ValueError("native AV diagnostic target is not the reviewed test camera")
    with _exclusive(camera_id):
        camera = registry.get_camera_by_id(camera_id)
        if camera is None:
            raise CameraNotFound("camera not found")
        enrollment = p2p.get_enrollment_for_camera(camera_id)
        if (enrollment is None or enrollment.camera_id != camera_id
                or enrollment.device_id != reviewed_device_id):
            raise ValueError("native AV diagnostic enrollment mismatch")
        return probe_av_route(enrollment, camera_id=camera_id, device_id=reviewed_device_id,
                              duration=3.0, cancelled=cancelled)
