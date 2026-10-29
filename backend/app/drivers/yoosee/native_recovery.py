"""Driver classification for a fully retired native media attempt, never renewal."""

from collections.abc import Callable

from ...media.recovering_source import RecoveringSource
from ...media.source_registry import ManagedSource
from .native_video_source import NativeVideoResult, NativeVideoSource
from .p2p.media_retirement import retired_assembly_timeout, retired_media_route
from .shared_control_reads import reserve_native_owner


class _OwnedRecovery(RecoveringSource):
    def __init__(self, initial: NativeVideoSource, *, camera_id: str,
                 factory: Callable[[], NativeVideoSource], valid: Callable[[], bool]):
        super().__init__(initial, factory=factory, valid=valid, retryable=retryable)
        self._camera_id = camera_id

    def run(self, *, cancelled: Callable[[], bool]) -> object:
        with reserve_native_owner(self._camera_id):
            return super().run(cancelled=cancelled)


def retryable(result: object, source: ManagedSource) -> bool:
    if not isinstance(result, NativeVideoResult) or not isinstance(source, NativeVideoSource):
        return False
    worker = result.session.worker
    released_timeout=worker is not None and result.reason=='transport' and retired_assembly_timeout(worker)
    decoder_timeout = (worker is not None and result.reason == 'consumer_failed'
        and result.failure_type == 'TimeoutError' and worker.reason == 'stopped'
        and retired_media_route(worker))
    if worker is not None and (
        worker.socket_close_failed
        or (worker.media is not None and worker.media.opened and not worker.media.broker_release)
        or (worker.tcp is not None and (worker.tcp.socket_close_failed
            or (not worker.tcp.close_acknowledged and not (released_timeout or decoder_timeout))))
        or (worker.motion is not None and worker.motion.start_attempted and not worker.motion.release_delivery_confirmed)
    ):
        return False
    if decoder_timeout:
        return True  # run() already joined the consumer and confirmed decoder exit.
    if result.reason in {"socket", "media_complete", "contended", "stopped", "busy",
                         "transport", "heartbeat_timeout", "lifetime"}:
        return True
    if result.reason == "connection":
        return source.diagnostics().get("session_connection_failure") in {
            "offline", "socket_timeout", "socket_error", "list_dns", "list_timeout",
        }
    # Format/protocol/evidence failures and stale authentication are not retried.
    return False


def wrap(initial: NativeVideoSource, *, camera_id: str, factory: Callable[[], NativeVideoSource],
         valid: Callable[[], bool]) -> RecoveringSource:
    return _OwnedRecovery(initial, camera_id=camera_id, factory=factory, valid=valid)
