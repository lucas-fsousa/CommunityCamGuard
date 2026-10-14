"""Finite PTZ socket owner; production activation remains disabled."""

import time
from collections.abc import Callable
from dataclasses import dataclass

from .account_output_budget import AccountOutputBudget
from .broker_read_socket import ReadSocket
from .broker_sequences import BrokerSequences
from .contracts import CertifiedNode
from .ptz_scheduler import PtzScheduler, ScheduledPtzResult
from .ptz_socket_pump import pump_ptz


@dataclass(frozen=True, slots=True)
class PtzTransportResult:
    motion: ScheduledPtzResult
    received_datagrams: int
    input_failed: bool
    socket_close_failed: bool


def run_ptz_transport(
    sock: ReadSocket, node: CertifiedNode, sequences: BrokerSequences,
    output: AccountOutputBudget, *, generation: int, access_id: int,
    device_id: int, direction: str, reviewed_directions: frozenset[str],
    duration: float = 0.2, cancelled: Callable[[], bool] = lambda: False,
    clock: Callable[[], float] = time.monotonic,
    wait: Callable[[float], None] = time.sleep,
) -> PtzTransportResult:
    """Transfer exclusive socket ownership for one authorized gesture and close.

    Caller holds account/enrollment exclusion. No login, socket creation or retry.
    Invalid admission also retires the namespace and closes the supplied socket.
    """
    close_failed = False
    try:
        job = PtzScheduler(node, sequences, output, generation=generation,
            access_id=access_id, device_id=device_id, direction=direction,
            reviewed_directions=reviewed_directions, duration=duration, clock=clock)
        result = pump_ptz(sock, node, job, output, generation=generation,
                          cancelled=cancelled, clock=clock, wait=wait)
    finally:
        sequences.close()
        try:
            sock.close()
        except OSError:
            close_failed = True
    return PtzTransportResult(result.motion, result.received_datagrams,
                              result.input_failed, close_failed)
