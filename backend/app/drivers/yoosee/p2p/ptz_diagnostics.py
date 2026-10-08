"""Fixed-label PTZ preparation diagnostics; no credentials or vendor replies."""

from .contracts import InitInfoRejectedError
from .control_ownership import ControlChannelBusy

_REASONS = {
    "native PTZ model profile is not supported": "unsupported_model",
    "native PTZ direction lacks current axis evidence": "unsupported_axis",
    "native PTZ identity does not match reviewed evidence": "identity_mismatch",
    "native PTZ correlated preflight read failed": "identity_read_failed",
    "selected P2P camera is not online": "offline",
    "PTZ broker liveness check failed": "broker_keepalive",
    "P2P access node did not answer heartbeat": "broker_heartbeat",
    "P2P list service could not be resolved": "list_dns",
    "P2P list service did not answer": "list_timeout",
    "no advertised P2P node accepted certification": "certification_unavailable",
    "certified P2P node did not return device inventory": "inventory_timeout",
    "no certified P2P node completed initialization": "inventory_unavailable",
    "P2P inventory probe exhausted its time budget": "preparation_budget",
    "P2P camera session exhausted its time budget": "preparation_budget",
    "P2P session expired and no renewable vendor account is configured": "renewal_unconfigured",
    "P2P enrollment disappeared during session renewal": "enrollment_missing",
    "P2P session renewal failed": "renewal_failed",
    "another P2P session did not release this camera in time": "camera_busy",
}


def preparation_failure_reason(error: Exception) -> str:
    """Describe the observed stage, never infer token expiry from a timeout.

    Exact local messages only; unknown messages and chained exceptions are never
    exported. Labels are diagnostic and must not drive retry or capability policy.
    """
    if isinstance(error, InitInfoRejectedError):
        return "stale_access" if error.error_code == 0x216B else "access_rejected"
    if isinstance(error, ControlChannelBusy):
        return "control_busy"
    if isinstance(error, TimeoutError):
        return "socket_timeout"
    if isinstance(error, OSError):
        return "socket_error"
    return _REASONS.get(str(error), "transport_or_auth")
