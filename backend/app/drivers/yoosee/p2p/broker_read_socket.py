"""Finite reads on a supplied authenticated UDP socket, with exclusive ownership.

No socket creation/login, threads, renewal, retries, camera actions or production
wiring. Caller transfers this socket; it is closed on every exit and never cached.
"""

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Protocol

from .broker_read_dispatch import BrokerReadDispatcher, ReadDispatchError
from .broker_reads import ReadCompletion
from .broker_transactions import MAX_PENDING
from .contracts import CertifiedNode


class ReadSocket(Protocol):
    def settimeout(self, value: float) -> None: ...
    def sendto(self, wire: bytes, peer: tuple[str, int]) -> int: ...
    def recvfrom(self, size: int) -> tuple[bytes, tuple[str, int]]: ...
    def close(self) -> None: ...


@dataclass(frozen=True, slots=True)
class ReadSpec:
    device_id: int = field(repr=False)
    path: str
    timeout: float = 2.0


@dataclass(frozen=True, slots=True)
class ReadExchange:
    completions: tuple[ReadCompletion, ...]
    failure: str | None
    sent_datagrams: int
    received_datagrams: int


class _Cancelled(Exception):
    pass


class _Deadline(Exception):
    pass


def run_owned_reads(sock: ReadSocket, node: CertifiedNode, generation: int,
                    specs: tuple[ReadSpec, ...], *, clock: Callable[[], float] = time.monotonic,
                    cancelled: Callable[[], bool] = lambda: False) -> ReadExchange:
    """One bounded batch, input-order sends and arrival-order completions.

    Caller must retain broker/camera ownership and verified enrollment bindings.
    A camera response remains evidence if sending its ACK later fails: failure is
    reported separately, never replaced by success or an automatic retry.
    """
    owner = None
    completed: list[ReadCompletion] = []
    sent = received = 0
    failure = None
    try:
        deadline = clock() + 15
        if not isinstance(specs, tuple) or not 1 <= len(specs) <= MAX_PENDING:
            raise ValueError("one bounded tuple of reads required")
        owner = BrokerReadDispatcher(node, generation, clock=clock)
        if any(not isinstance(spec, ReadSpec) for spec in specs):
            raise ValueError("invalid read specification")
        # Validate every request before the first network write.
        prepared = [owner.prepare(spec.device_id, spec.path, spec.timeout) for spec in specs]

        def check() -> None:
            if cancelled():
                raise _Cancelled
            if clock() >= deadline:
                raise _Deadline

        def send(wire: bytes) -> None:
            nonlocal sent
            check()
            sock.settimeout(min(0.2, max(0.001, deadline - clock())))
            sent += 1  # Counts attempted writes, including ambiguous failures.
            if sock.sendto(wire, node.address) != len(wire):
                raise OSError("partial broker datagram")

        for request in prepared:
            check()
            completed.extend(owner.poll())
            if any(result.request.ticket == request.request.ticket for result in completed):
                continue  # Never send a request whose deadline elapsed during earlier writes.
            send(request.wire)
        while owner.pending_count:
            check()
            completed.extend(owner.poll())
            if not owner.pending_count:
                break
            sock.settimeout(min(0.05, max(0.001, deadline - clock())))
            try:
                wire, peer = sock.recvfrom(4096)
            except TimeoutError:
                continue
            received += 1
            check()
            batch = owner.receive(wire, peer, generation=generation)
            completed.extend(batch.completions)
            for reply in batch.acknowledgements:
                send(reply)
    except ReadDispatchError as exc:
        completed.extend(exc.cancelled)
        failure = "protocol"
    except _Cancelled:
        failure = "cancelled"
    except _Deadline:
        failure = "deadline"
    except OSError:
        failure = "socket"
    except ValueError:
        failure = "invalid"
    finally:
        try:
            if owner is not None:
                completed.extend(owner.close())
        finally:
            try:
                sock.close()
            except OSError:
                failure = failure or "socket_close"
    return ReadExchange(tuple(completed), failure, sent, received)
