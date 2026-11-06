"""Camera-local recovery inside an already owned account; never reserves/logs in."""

from collections.abc import Callable

from ...media.recovering_source import RecoveringSource
from ...media.source_registry import ManagedSource
from .native_retry_diagnostics import record as record_retry
from .native_video_source import NativeVideoResult, NativeVideoSource

_local_failures = frozenset({"TimeoutError", "Mp4SizeError", "Mp4OrderError",
    "Mp4CapacityError", "DecoderExitedError", "BrokenPipeError", "OSError"})


def retryable(result: object, source: ManagedSource) -> bool:
    if not isinstance(result, NativeVideoResult) or not isinstance(source, NativeVideoSource):
        return False
    details = source.diagnostics()
    if (details.get("session_scope") != "media_lease"
            or details.get("session_lease_detached") is not True
            or details.get("consumer_alive") is not False
            or details.get("cleanup_failed") is not False):
        return False
    # A shared broker intentionally remains open. Only the camera's confirmed
    # detach plus consumer/decoder retirement permits another lease request.
    if result.reason == "consumer_failed":
        return result.failure_type in _local_failures
    return result.reason in {"socket", "media_complete", "stopped"}


def wrap(initial: NativeVideoSource, *, camera_id: str, factory: Callable[[], NativeVideoSource],
         valid: Callable[[], bool]) -> RecoveringSource:
    """The factory must bind the retained account, not authenticate another one."""
    def inspect(result: object, source: ManagedSource) -> bool:
        allowed = retryable(result, source)
        if allowed and isinstance(result, NativeVideoResult) and isinstance(source, NativeVideoSource):
            record_retry(camera_id, result, source)
        return allowed
    return RecoveringSource(initial, factory=factory, valid=valid, retryable=inspect)
