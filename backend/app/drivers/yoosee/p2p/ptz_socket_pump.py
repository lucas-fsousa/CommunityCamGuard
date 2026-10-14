"""Experimental finite UDP PTZ owner; deliberately not wired into production."""

import time
from collections.abc import Callable
from dataclasses import dataclass

from .account_output_budget import AccountOutputBudget
from .broker_maintenance import BrokerMaintenance
from .broker_read_socket import ReadSocket
from .contracts import CertifiedNode
from .ptz_scheduler import PtzPacket, PtzScheduler, ScheduledPtzResult


@dataclass(frozen=True, slots=True)
class PtzPumpResult:
    motion: ScheduledPtzResult
    received_datagrams: int
    input_failed: bool
    sent_datagrams: int


def pump_ptz(
    sock: ReadSocket, node: CertifiedNode, job: PtzScheduler,
    output: AccountOutputBudget, *, generation: int,
    cancelled: Callable[[], bool] = lambda: False,
    clock: Callable[[], float] = time.monotonic,
    wait: Callable[[float], None] = time.sleep,
) -> PtzPumpResult:
    """Drive an existing job on the sole owner's socket; never close or log in.

    Caller must hold account exclusion and authorize the exact device/profile.
    Caller retains socket and namespace ownership, including physical close.
    No authentication, socket creation, other reader or replacement is allowed.
    Mode-2 frames may be fragmented; only the scheduler owns message receipts.
    Interrupts drain STOP before re-raising. This is not a reusable account worker.
    Clock/wait injection exists for deterministic offline tests only.
    """
    input_failed = False
    received = 0
    sent = 0
    pending_error: BaseException | None = None
    maintenance = BrokerMaintenance(node, clock=clock, acknowledge_messages=False)
    try:
        def send(packet: PtzPacket) -> None:
            nonlocal sent
            # Scheduler already charged this attempt. Never replay START even if
            # sendto failed after the kernel may have accepted the datagram.
            try:
                sock.settimeout(0.02)
                sent += 1
                if sock.sendto(packet.wire, node.address) != len(packet.wire):
                    job.send_failed()
            except OSError:
                job.send_failed()

        while job.result is None:
            try:
                if pending_error is None and cancelled():
                    job.cancel()
                packet = job.next_packet()
                if packet is not None:
                    send(packet)
                if job.result is not None:
                    break
                if input_failed:
                    wait(0.02)
                    continue
                sock.settimeout(0.02)
                try:
                    wire, peer = sock.recvfrom(4097)
                except TimeoutError:
                    continue
                except OSError:
                    input_failed = True
                    job.cancel()
                    continue
                received += 1
                if received > 64 or len(wire) > 4096:
                    input_failed = True
                    job.cancel()
                    continue
                messages: list[bytes] = []
                try:
                    fragment_acks = maintenance.receive(wire, peer, inspect_message=messages.append)
                    for ack in fragment_acks:
                        # Fragment ACKs use ordinary capacity, never STOP slots.
                        output.charge(len(ack))
                        send(PtzPacket("fragment", ack))
                except ValueError:
                    input_failed = True
                    job.cancel()
                    continue
                for plain in messages:
                    for receipt in job.observe(plain, peer, generation=generation):
                        send(receipt)
            except BaseException as exc:
                # Preserve the original interrupt/error, disable input/callbacks,
                # but retain this same socket for the finite RELEASE attempts.
                if pending_error is None:
                    pending_error = exc
                input_failed = True
                job.send_failed()
                # A repeated interruption in wait must not create a tight loop.
                if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                    continue
                if job.result is None:
                    # Transport callbacks may fail; scheduler itself must remain
                    # healthy for cleanup. Its next step runs outside the handler.
                    packet = job.next_packet()
                    if packet is not None:
                        send(packet)
        result = job.result
    finally:
        maintenance.close()
    if pending_error is not None:
        raise pending_error
    assert result is not None
    return PtzPumpResult(result, received, input_failed, sent)
