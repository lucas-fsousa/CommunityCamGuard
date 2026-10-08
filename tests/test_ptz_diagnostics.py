"""Preparation labels never expose exception text or imply timeout == expiry."""

import pytest

from backend.app.drivers.yoosee.p2p.contracts import InitInfoRejectedError, P2PProbeError
from backend.app.drivers.yoosee.p2p.control_ownership import ControlChannelBusy
from backend.app.drivers.yoosee.p2p.ptz_diagnostics import preparation_failure_reason


@pytest.mark.parametrize("message,label", [
    ("native PTZ model profile is not supported", "unsupported_model"),
    ("native PTZ direction lacks current axis evidence", "unsupported_axis"),
    ("native PTZ identity does not match reviewed evidence", "identity_mismatch"),
    ("native PTZ correlated preflight read failed", "identity_read_failed"),
    ("selected P2P camera is not online", "offline"),
    ("PTZ broker liveness check failed", "broker_keepalive"),
    ("P2P access node did not answer heartbeat", "broker_heartbeat"),
    ("P2P list service could not be resolved", "list_dns"),
    ("P2P list service did not answer", "list_timeout"),
    ("no advertised P2P node accepted certification", "certification_unavailable"),
    ("certified P2P node did not return device inventory", "inventory_timeout"),
    ("no certified P2P node completed initialization", "inventory_unavailable"),
    ("P2P inventory probe exhausted its time budget", "preparation_budget"),
    ("P2P camera session exhausted its time budget", "preparation_budget"),
    ("P2P session expired and no renewable vendor account is configured", "renewal_unconfigured"),
    ("P2P enrollment disappeared during session renewal", "enrollment_missing"),
    ("P2P session renewal failed", "renewal_failed"),
    ("another P2P session did not release this camera in time", "camera_busy"),
])
def test_local_stage_messages(message, label):
    assert preparation_failure_reason(P2PProbeError(message)) == label


@pytest.mark.parametrize("error,label", [
    (InitInfoRejectedError(0x216B), "stale_access"),
    (InitInfoRejectedError(123), "access_rejected"),
    (ControlChannelBusy("private-detail"), "control_busy"),
    (TimeoutError("private-detail"), "socket_timeout"),
    (OSError("private-detail"), "socket_error"),
    (P2PProbeError("private-detail"), "transport_or_auth"),
    (ValueError("private-detail"), "transport_or_auth"),
    (P2PProbeError("P2P list service did not answer private-detail"), "transport_or_auth"),
])
def test_typed_failures_and_unknown_text_are_redacted(error, label):
    error.__cause__ = RuntimeError("private-token-in-cause")
    assert preparation_failure_reason(error) == label
