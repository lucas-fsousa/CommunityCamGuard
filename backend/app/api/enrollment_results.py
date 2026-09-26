"""Strict scalar contracts for non-mutating enrollment diagnostics."""

from .enrollment_errors import EnrollmentFailure, enrollment_failure

_BOOLS = {
    "online": ("query_succeeded", "online", "terminal_failure", "privileged_handoff_ready"),
    "inventory": ("authenticated", "target_visible", "target_online", "target_term_resolved", "camera_contacted"),
    "route": ("authenticated", "target_visible", "target_online", "broker_acknowledged",
              "route_advertised", "direct_handshake", "camera_contacted", "media_opened", "command_sent"),
}
_COUNTS = {
    "online": (), "inventory": ("device_count", "online_count", "skipped_incomplete_nodes"),
    "route": ("direct_datagrams",),
}
_CODES = {"online": ("code",), "inventory": (), "route": ("broker_error_code",)}


def diagnostic_result(kind: str, payload: dict[str, object], *, device_id: str) -> dict[str, object]:
    """Never coerce values or include unknown provider attributes in a response."""
    result: dict[str, object] = {"device_id": device_id}
    for field in _BOOLS[kind]:
        value = payload.get(field)
        if type(value) is not bool:
            raise enrollment_failure(EnrollmentFailure.INVALID_DIAGNOSTIC)
        result[field] = value
    for field in _COUNTS[kind]:
        value = payload.get(field)
        if type(value) is not int or not 0 <= value < 2**31:
            raise enrollment_failure(EnrollmentFailure.INVALID_DIAGNOSTIC)
        result[field] = value
    for field in _CODES[kind]:
        value = payload.get(field)
        if value is not None and (type(value) is not int or not -(2**31) <= value < 2**32):
            raise enrollment_failure(EnrollmentFailure.INVALID_DIAGNOSTIC)
        result[field] = value
    return result
