"""Bounded OFF drain on the pinned old socket, after the normal reader has stopped."""

import time
from collections.abc import Callable

from .account_output_budget import AccountOutputBudget
from .broker_maintenance import BrokerMaintenance
from .broker_read_socket import ReadSocket
from .contracts import CertifiedNode
from .siren_pulse_job import SirenPacket
from .siren_pulse_pump import SirenPulsePump


def drain_siren(sock: ReadSocket, node: CertifiedNode, pulse: SirenPulsePump, output: AccountOutputBudget, *, generation: int,
                clock: Callable[[], float] = time.monotonic,
                wait: Callable[[float], None] = time.sleep) -> tuple[int, int]:
    pulse.cancel()
    sent = received = 0
    broken_input = False
    pending: BaseException | None = None
    maintenance = BrokerMaintenance(node, clock=clock, acknowledge_messages=False)
    deadline = clock()+2.5
    try:
        def send(packet: SirenPacket) -> None:
            nonlocal sent
            try:
                if packet.kind == 'fragment_receipt': output.charge(len(packet.wire))
                sock.settimeout(0.02)
                sent += 1
                if sock.sendto(packet.wire, node.address) != len(packet.wire):
                    pulse.send_failed()
            except OSError:
                pulse.send_failed()

        while clock() < deadline:
            try:
                for packet in pulse.poll():
                    send(packet)
                if not pulse.active:
                    break
                if broken_input:
                    wait(0.02)
                    continue
                sock.settimeout(0.02)
                try:
                    wire, peer = sock.recvfrom(4097)
                except TimeoutError:
                    continue
                except OSError:
                    broken_input = True
                    continue
                received += 1
                if received > 64:
                    broken_input = True
                    continue
                complete: list[bytes] = []
                try:
                    acks = maintenance.receive(wire, peer, inspect_message=complete.append)
                    for ack in acks:
                        send(SirenPacket('fragment_receipt', ack))
                    for plain in complete:
                        for packet in pulse.observe(plain, peer, generation=generation):
                            send(packet)
                except ValueError:
                    broken_input = True
            except BaseException as exc:
                if pending is None: pending = exc
                pulse.cancel()
                broken_input = True
                wait(0.02)
        if pulse.active:
            raise RuntimeError('native siren OFF cleanup deadline exceeded')
        pulse.poll()  # Publish the terminal result before releasing the old socket.
    finally:
        maintenance.close()
    if pending is not None:
        raise pending
    return sent, received
