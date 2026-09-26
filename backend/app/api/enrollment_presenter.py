"""Vendor-neutral public contract for privileged enrollment status."""

from .enrollment_errors import EnrollmentFailure, enrollment_failure

_FLAGS = (
    "handoff_ready", "bound", "subscription_material_ready", "p2p_access_ready", "rtsp_ready",
)


def enrollment_status(payload: object, *, device_id: str) -> dict[str, object]:
    """Do not serialize a driver's arbitrary dictionary or coerce readiness values."""
    if not isinstance(payload, dict):
        raise enrollment_failure(EnrollmentFailure.INVALID_RESULT)
    if type(payload.get("device_id")) is not str or payload["device_id"] != device_id:
        raise enrollment_failure(EnrollmentFailure.INVALID_RESULT)
    if any(type(payload.get(flag)) is not bool for flag in _FLAGS):
        raise enrollment_failure(EnrollmentFailure.INVALID_RESULT)
    expires = payload.get("expires_in")
    if type(expires) is not int or not 0 <= expires < 2**31:
        raise enrollment_failure(EnrollmentFailure.INVALID_RESULT)
    return {"device_id": device_id, "expires_in": expires,
            **{flag: payload[flag] for flag in _FLAGS}}
