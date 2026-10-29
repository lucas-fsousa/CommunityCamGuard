"""Driver classification for a fully retired native media attempt, never renewal."""

from collections.abc import Callable

from ...media.recovering_source import RecoveringSource
from ...media.source_registry import ManagedSource
from .native_video_source import NativeVideoResult, NativeVideoSource


def retryable(result: object, source: ManagedSource) -> bool:
    if not isinstance(result, NativeVideoResult) or not isinstance(source, NativeVideoSource):
        return False
    worker = result.session.worker
    if worker is not None and (
        worker.socket_close_failed
        or (worker.media is not None and worker.media.opened and not worker.media.broker_release)
        or (worker.tcp is not None and (not worker.tcp.close_acknowledged or worker.tcp.socket_close_failed))
        or (worker.motion is not None and worker.motion.start_attempted and not worker.motion.release_delivery_confirmed)
    ):
        return False
    if result.reason in {"socket", "media_complete", "contended", "stopped"}:
        return True
    if result.reason == "connection":
        return source.diagnostics().get("session_connection_failure") in {
            "offline", "socket_timeout", "socket_error", "list_dns", "list_timeout",
        }
    # Format/protocol/evidence failures and stale authentication are not retried.
    return False


def wrap(initial: NativeVideoSource, *, factory: Callable[[], NativeVideoSource],
         valid: Callable[[], bool]) -> RecoveringSource:
    return RecoveringSource(initial, factory=factory, valid=valid, retryable=retryable)
