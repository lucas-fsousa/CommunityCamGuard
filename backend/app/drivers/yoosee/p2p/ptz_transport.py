"""Experimental finite UDP PTZ owner; deliberately not wired into production."""

import socket
import time
from collections.abc import Callable
from dataclasses import dataclass

from .account_output_budget import AccountOutputBudget
from .broker_sequences import BrokerSequences
from .contracts import CertifiedNode
from .ptz_scheduler import PtzPacket, PtzScheduler, ScheduledPtzResult
from .session_io import decrypt_node_frame


@dataclass(frozen=True, slots=True)
class PtzTransportResult:
    motion: ScheduledPtzResult
    received_datagrams: int
    input_failed: bool
    socket_close_failed: bool


def run_ptz_transport(
    sock: socket.socket, node: CertifiedNode, sequences: BrokerSequences,
    output: AccountOutputBudget, *, generation: int, access_id: int,
    device_id: int, direction: str, reviewed_directions: frozenset[str],
    duration: float = 0.2, cancelled: Callable[[], bool] = lambda: False,
    clock: Callable[[], float] = time.monotonic,
    wait: Callable[[float], None] = time.sleep,
) -> PtzTransportResult:
    """Take exclusive ownership of a socket, run one gesture, then close it.

    Caller must hold account exclusion and authorize the exact device/profile.
    No authentication, socket creation, other reader or replacement is allowed.
    Direct mode-2 frames only; fragmented replies are not supported here yet.
    Interrupts drain STOP before re-raising. This is not a reusable account worker.
    Clock/wait injection exists for deterministic offline tests only.
    """
    close_failed = input_failed = False
    received = 0
    pending_error: BaseException | None = None
    try:
        job = PtzScheduler(node, sequences, output, generation=generation,
            access_id=access_id, device_id=device_id, direction=direction,
            reviewed_directions=reviewed_directions, duration=duration, clock=clock)

        def send(packet: PtzPacket) -> None:
            # Scheduler already charged this attempt. Never replay START even if
            # sendto failed after the kernel may have accepted the datagram.
            try:
                sock.settimeout(0.02)
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
                # Reject other peers and unencrypted/legacy modes before decode.
                # Only the scheduler creates receipts: no second generic ACK path.
                if peer == node.address and len(wire) >= 32 and wire[22] & 3 == 2:
                    plain = decrypt_node_frame(wire, node)
                    if plain is not None:
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
        try:
            sock.close()
        except OSError:
            close_failed = True
    if pending_error is not None:
        raise pending_error
    assert result is not None
    return PtzTransportResult(result, received, input_failed, close_failed)
