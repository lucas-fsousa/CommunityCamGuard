"""Camera-local recovery inside an already owned account; never reserves/logs in."""

from collections.abc import Callable

from ...db.p2p import P2PEnrollment
from ...media.recovering_source import RecoveringSource
from ...media.source_registry import ManagedSource
from .account_video_source import AccountVideoSource
from .native_account_service import NativeAccounts
from .native_retry_diagnostics import record as record_retry
from .native_video_source import NativeVideoResult, NativeVideoSource
from .p2p.media_failures import TRANSIENT_CODES

_local_failures = frozenset({"TimeoutError", "Mp4SizeError", "Mp4OrderError",
    "Mp4CapacityError", "DecoderExitedError", "BrokenPipeError", "OSError"})


def retryable(result: object, source: ManagedSource) -> bool:
    if not isinstance(result, NativeVideoResult) or not isinstance(source, (NativeVideoSource, AccountVideoSource)):
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
    if result.reason == "protocol":
        return details.get("session_protocol_code") in TRANSIENT_CODES
    if result.reason == "connection":
        return details.get("session_connection_failure") in {"offline", "socket_timeout", "socket_error",
            "list_dns", "list_timeout", "broker_heartbeat", "certification_unavailable",
            "inventory_timeout", "inventory_unavailable", "preparation_budget"}
    if result.reason == "busy":
        return isinstance(source, AccountVideoSource)
    # An account ending is not itself unsafe: the checks above still require
    # the exact lease's confirmed detach and fully retired consumer. The service
    # will reuse or replace its account only after its own ownership checks.
    return result.reason in {"socket", "media_complete", "stopped", "account_ended"}


class AccountRecoveringSource(RecoveringSource):
    def __init__(self, *args, accounts: NativeAccounts, entry: P2PEnrollment, **kwargs):
        super().__init__(*args, **kwargs)
        self._accounts, self._entry = accounts, entry

    def run(self, *, cancelled: Callable[[], bool]) -> object:
        with self._accounts.source_intent(self._entry):
            return super().run(cancelled=cancelled)


def wrap(initial: NativeVideoSource | AccountVideoSource, *, camera_id: str,
         factory: Callable[[], NativeVideoSource | AccountVideoSource],
         valid: Callable[[], bool], accounts: NativeAccounts | None = None,
         entry: P2PEnrollment | None = None) -> RecoveringSource:
    """The factory must bind the retained account, not authenticate another one."""
    def inspect(result: object, source: ManagedSource) -> bool:
        allowed = retryable(result, source)
        if allowed and isinstance(result, NativeVideoResult) and isinstance(source, (NativeVideoSource, AccountVideoSource)):
            record_retry(camera_id, result, source)
        return allowed
    if accounts is not None:
        if entry is None:
            raise ValueError("account source requires an enrolled camera")
        return AccountRecoveringSource(initial, factory=factory, valid=valid, retryable=inspect,
                                       accounts=accounts, entry=entry)
    return RecoveringSource(initial, factory=factory, valid=valid, retryable=inspect)
