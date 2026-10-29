"""A timed-out inner stream may retire through its confirmed outer route release."""

from .account_read_worker import WorkerSummary


def retired_assembly_timeout(worker: WorkerSummary) -> bool:
    return (worker.reason=='protocol' and worker.protocol_code=='kcp_assembly_timeout'
        and not worker.socket_close_failed and worker.media is not None
        and worker.media.opened and worker.media.broker_release
        and worker.tcp is not None and worker.tcp.ready and not worker.tcp.socket_close_failed
        and (worker.motion is None or not worker.motion.start_attempted
             or worker.motion.release_delivery_confirmed))
