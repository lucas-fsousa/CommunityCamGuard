"""Fixed identity batch through the real finite worker; no authentication or threads."""

import time
from collections.abc import Callable
from dataclasses import dataclass, field

from .account_read_worker import AccountReadWorker, WorkerSummary
from .broker_read_socket import ReadSocket, ReadSpec
from .broker_reads import ReadCompletion
from .contracts import ACCOUNT_IDENTITY_READ_PATHS, CertifiedNode


@dataclass(frozen=True, slots=True)
class WorkerCheck:
    completions: tuple[ReadCompletion, ...] = field(repr=False)
    worker: WorkerSummary
    failure: str | None


def run_owned_worker_check(sock: ReadSocket, node: CertifiedNode, generation: int,
                           specs: tuple[ReadSpec, ...], local_ip: str, local_port: int, *,
                           clock: Callable[[], float] = time.monotonic,
                           cancelled: Callable[[], bool] = lambda: False) -> WorkerCheck:
    """Transfer ownership; three fixed reads, one heartbeat, <=60 seconds, no retry.

    The server retains camera/account reservations through physical socket close.
    This is not proof of reads after heartbeat or production concurrent PTZ.
    """
    worker = None
    transferred = False
    try:
        if (not isinstance(specs, tuple) or len(specs) != 3
                or any(not isinstance(spec, ReadSpec) for spec in specs)
                or tuple(s.path for s in specs) != ACCOUNT_IDENTITY_READ_PATHS
                or len({s.device_id for s in specs}) != 1):
            raise ValueError("fixed single-camera identity batch required")
        worker = AccountReadWorker(node, generation, local_ip, local_port, clock=clock)
        tickets = tuple(worker.mailbox.submit(s.device_id, s.path, s.timeout) for s in specs)
        transferred = True
        summary = worker.run(sock, cancelled=cancelled)
        outcomes = tuple(worker.mailbox.take(ticket) for ticket in tickets)
        completions = tuple(o.completion for o in outcomes if o is not None and o.completion is not None)
        failure = None
        if summary.socket_close_failed:
            failure = "socket_close"
        elif summary.reason != "deadline":
            failure = summary.reason
        elif len(completions) != 3 or any(r.status != "response" or r.error_code != 0 for r in completions):
            failure = "read_incomplete"
        elif summary.heartbeat_responses != 1:
            failure = "heartbeat_incomplete"
        return WorkerCheck(completions, summary, failure)
    finally:
        if not transferred:
            if worker is not None:
                worker.mailbox.close()
            sock.close()
