"""Bounded broker-only CLOSE drain on the existing sole owner's socket."""

import time
from collections.abc import Callable

from .account_output_budget import AccountOutputBudget
from .broker_maintenance import BrokerMaintenance
from .broker_read_socket import ReadSocket
from .contracts import CertifiedNode
from .media_route_job import MediaRouteJob


def close_media_route(sock: ReadSocket, node: CertifiedNode, job: MediaRouteJob,
                      output: AccountOutputBudget, *, generation: int,
                      clock: Callable[[], float], wait: Callable[[float], None] = time.sleep
                      ) -> tuple[int, int]:
    """No login/socket creation, retries or physical socket close. Caller owns it.

    The media TCP owner must already have closed its own channel. This routine
    deals only with the broker receipt, not remote resource-reclamation proof.
    """
    job.cancel()
    sent = received = 0
    input_failed = False
    owner = BrokerMaintenance(node, clock=clock)
    try:
        while job.result is None:
            packet = job.next_packet()
            if packet is not None:
                sock.settimeout(0.02)
                sent += 1
                try:
                    if sock.sendto(packet.wire, node.address) != len(packet.wire):
                        job.send_failed()
                except OSError:
                    job.send_failed()
            if job.result is not None:
                break
            if input_failed:
                wait(0.02)
                continue
            try:
                sock.settimeout(0.02)
                wire, peer = sock.recvfrom(4097)
                received += 1
                if received > 64:
                    raise ValueError("cleanup input budget")
                complete: list[bytes] = []
                replies = owner.receive(wire, peer, inspect_message=complete.append)
                for plain in complete:
                    job.observe(plain, peer, generation=generation)
                for reply in replies:
                    output.charge(len(reply))
                    sent += 1
                    if sock.sendto(reply, node.address) != len(reply):
                        raise OSError("partial cleanup reply")
            except TimeoutError:
                continue
            except (OSError, ValueError):
                input_failed = True
    finally:
        owner.close()
    return sent, received
