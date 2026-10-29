"""A timed-out inner stream may retire through its confirmed outer route release."""

from .account_read_worker import WorkerSummary
from .media_failures import TRANSIENT_CODES


def retired_media_route(worker: WorkerSummary, *, require_ready: bool = True) -> bool:
    """Physical closure plus confirmed outer release; not a retry classification."""
    return (not worker.socket_close_failed and worker.media is not None
        and worker.media.opened and worker.media.broker_release
        and worker.tcp is not None and (worker.tcp.ready or not require_ready) and not worker.tcp.socket_close_failed
        and (worker.motion is None or not worker.motion.start_attempted
             or worker.motion.release_delivery_confirmed))


def retired_assembly_timeout(worker: WorkerSummary) -> bool:
    return (worker.reason=='protocol' and worker.protocol_code=='kcp_assembly_timeout'
            and retired_media_route(worker))


def retired_transport_failure(worker: WorkerSummary) -> bool:
    # A failed initial handshake also needs another attempt after physical
    # retirement. Readiness of the old failed connection is not a prerequisite.
    return ((worker.reason == 'socket' or
             (worker.reason == 'protocol' and worker.protocol_code in TRANSIENT_CODES))
            and retired_media_route(worker, require_ready=False))
