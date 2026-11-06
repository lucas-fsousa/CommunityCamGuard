import pytest

from backend.app.drivers.yoosee.native_lease_recovery import retryable
from backend.app.drivers.yoosee.native_video_source import NativeVideoResult, NativeVideoSource
from backend.app.drivers.yoosee.shared_read_session import SharedReadResult


@pytest.mark.parametrize("reason,failure,allowed", [("socket", "", True),
    ("media_complete", "", True), ("stopped", "", True), ("consumer_failed", "TimeoutError", True),
    ("consumer_failed", "DecoderExitedError", True), ("consumer_failed", "ValueError", False),
    ("protocol", "", False), ("connection", "", False), ("busy", "", False),
    ("enrollment_changed", "", False), ("account_ended", "", False)])
def test_camera_lease_retry_does_not_require_closing_shared_account(monkeypatch, reason, failure, allowed):
    source = NativeVideoSource("test", "123")
    monkeypatch.setattr(source, "diagnostics", lambda: {"session_scope": "media_lease",
        "session_lease_detached": True, "consumer_alive": False, "cleanup_failed": False,
        "session_broker_socket_closed": False})
    result = NativeVideoResult(reason, SharedReadResult(reason), failure)
    assert retryable(result, source) is allowed


@pytest.mark.parametrize("fault", ["scope", "detach", "consumer", "decoder"])
def test_uncertain_source_or_decoder_retirement_cannot_retry(monkeypatch, fault):
    source = NativeVideoSource("test", "123")
    details = {"session_scope": "account" if fault == "scope" else "media_lease",
        "session_lease_detached": fault != "detach", "consumer_alive": fault == "consumer",
        "cleanup_failed": fault == "decoder"}
    monkeypatch.setattr(source, "diagnostics", lambda: details)
    assert not retryable(NativeVideoResult("socket", SharedReadResult("socket")), source)
