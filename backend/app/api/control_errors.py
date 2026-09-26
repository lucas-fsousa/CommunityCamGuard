"""Public error projection: never serialize driver/transport exception messages."""

from fastapi import HTTPException

from ..drivers import ControlNotReady, Unsupported
from ..services.camera_controls import CameraNotFound, ControlBusy


def control_failure(exc: Exception, *, unsupported: str = "this camera doesn't support that control") -> HTTPException:
    if isinstance(exc, CameraNotFound):
        code, detail = 404, "camera not found"
    elif isinstance(exc, Unsupported):
        code, detail = 501, unsupported
    elif isinstance(exc, ControlBusy):
        code, detail = 409, "camera is busy; try again shortly"
    elif isinstance(exc, ControlNotReady):
        code, detail = 409, "camera control is not ready; check connection and session"
    else:
        code, detail = 502, "camera control failed"
    return HTTPException(code, detail, headers={"Cache-Control": "no-store"})
