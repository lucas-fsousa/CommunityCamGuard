"""Experimental finite read-only preparation under the native account boundary."""

import time
from collections.abc import Callable
from dataclasses import dataclass

from ...db.p2p import P2PEnrollment
from .native_ptz import reserve_read_handoff
from .p2p.account_read_connect import ReadConnectFailure, open_read_connection
from .p2p.account_read_lifecycle import AccountReadLifecycle
from .p2p.account_read_worker import WorkerSummary
from .p2p.broker_read_socket import ReadSpec
from .p2p.contracts import ACCOUNT_IDENTITY_READ_PATHS, P2PProbeError


@dataclass(frozen=True, slots=True)
class IdentityObservation:
    path: str
    status: str
    transport_result: int | None
    error_code: int | None


@dataclass(frozen=True, slots=True)
class ReadPreparation:
    failure: str | None
    responses: int
    worker: WorkerSummary | None = None
    observations: tuple[IdentityObservation, ...] = ()


def prepare_identity_reads(enrollment: P2PEnrollment, *, camera_id: str, device_id: str,
                            cancelled: Callable[[], bool] = lambda: False,
                            clock: Callable[[], float] = time.monotonic) -> ReadPreparation:
    """Server-owned finite adapter, not a periodic PTZ warmup replacement.

    Caller holds the camera/enrollment reservation through this call. This adapter
    owns broker/cache retirement through authentication, reads and physical close.
    No thread, automatic retry/renewal, arbitrary command, capability grant or
    automatic activation. Successful execution closes the connection, not pools it.
    """
    if (not camera_id or not device_id or enrollment.camera_id != camera_id
            or enrollment.device_id != device_id or len(device_id) > 20 or not device_id.isascii()
            or not device_id.isdecimal() or not 0 < int(device_id) < 1 << 64):
        raise ValueError("read preparation enrollment mismatch")
    if cancelled():
        return ReadPreparation("cancelled", 0)
    specs = tuple(ReadSpec(int(device_id), path, 2.0) for path in ACCOUNT_IDENTITY_READ_PATHS)
    with reserve_read_handoff():
        if cancelled():
            return ReadPreparation("cancelled", 0)
        lifecycle = AccountReadLifecycle(clock=clock)
        try:
            result = lifecycle.run_reads(lambda _: open_read_connection(
                enrollment, camera_id=camera_id, device_id=device_id, cancelled=cancelled),
                specs, cancelled=cancelled)
        except ReadConnectFailure:
            # The factory explicitly confirmed resource cleanup; a DNS timeout
            # or rejected authentication is not an uncertain live broker socket.
            return ReadPreparation("connection", 0)
        if result.worker.socket_close_failed:
            raise P2PProbeError("read preparation socket cleanup unconfirmed")
        responses = sum(outcome is not None and outcome.completion is not None
                        and outcome.status == "response"
                        # Camera response may precede the separate transport ACK.
                        and outcome.completion.transport_result in (None, 0)
                        and outcome.completion.error_code == 0 for outcome in result.outcomes)
        failure = None
        if result.worker.reason != "deadline":
            failure = result.worker.reason
        elif responses != 3:
            failure = "read_incomplete"
        elif result.worker.heartbeat_responses != 1:
            failure = "heartbeat_incomplete"
        observations = tuple(IdentityObservation(spec.path,
            outcome.status if outcome is not None else "missing",
            outcome.completion.transport_result if outcome and outcome.completion else None,
            outcome.completion.error_code if outcome and outcome.completion else None)
            for spec, outcome in zip(specs, result.outcomes, strict=True))
        return ReadPreparation(failure, responses, result.worker, observations)
