"""Finite exclusive encrypted socket pump; concurrent callers use only its mailbox."""

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass

from .account_output_budget import AccountOutputBudget, OutputExhausted
from .account_read_dispatcher import AccountReadDispatcher
from .broker_account import AccountEvents
from .broker_maintenance import BrokerMaintenance
from .broker_read_socket import ReadSocket
from .contracts import CertifiedNode


@dataclass(frozen=True, slots=True)
class WorkerSummary:
    reason: str
    sent_datagrams: int
    received_datagrams: int
    heartbeat_responses: int
    socket_close_failed: bool


class _Stop(Exception):
    def __init__(self, reason: str):
        self.reason = reason


class AccountReadWorker:
    """Run exactly once with a transferred socket, <=60 seconds, no auth/retries.

    Creates no thread. The caller retains enrollment and exclusive shared-account
    reservations until run returns. Never supply a socket borrowed from PTZ/cache.
    submit/cancel/take through mailbox and stop are safe from other caller threads.
    """

    def __init__(self, node: CertifiedNode, generation: int, local_ip: str, local_port: int, *,
                 clock: Callable[[], float] = time.monotonic):
        self._dispatcher = AccountReadDispatcher(node, generation, local_ip, local_port, clock=clock)
        self.mailbox = self._dispatcher.mailbox
        self._node: CertifiedNode | None = node
        self._generation, self._clock = generation, clock
        self._stop = threading.Event()
        self._drain = threading.Event()
        self._claim = threading.Lock()
        self._started = False
        self._socket: ReadSocket | None = None

    def stop(self) -> None:
        self._stop.set()

    def drain(self) -> None:
        """Request no new work, with <=2 seconds for already active replies."""
        self._drain.set()

    def run(self, sock: ReadSocket, *, cancelled: Callable[[], bool] = lambda: False) -> WorkerSummary:
        # A rejected second invocation still owns/closes its supplied socket,
        # but must not tear down the first invocation's account or mailbox.
        with self._claim:
            accepted = not self._started
            self._started = True
            same_active_socket = sock is self._socket
            if accepted:
                self._socket = sock
        if not accepted:
            failed = False
            try:
                if not same_active_socket:
                    sock.close()
            except OSError:
                failed = True
            return WorkerSummary("already_started", 0, 0, 0, failed)

        sent = received = beats = 0
        output = AccountOutputBudget()
        reason = "stopped"
        close_failed = False
        maintenance = None
        node = self._node
        try:
            assert node is not None
            deadline = self._clock() + 60
            maintenance = BrokerMaintenance.account_check(node, clock=self._clock)

            def check() -> None:
                if self._stop.is_set() or cancelled():
                    raise _Stop("stopped")
                if self._clock() >= deadline:
                    raise _Stop("deadline")

            def send(wire: bytes) -> None:
                nonlocal sent
                check()
                try:
                    output.charge(len(wire))
                except OutputExhausted:
                    raise _Stop("output_budget") from None
                sock.settimeout(min(0.2, max(0.001, deadline - self._clock())))
                sent += 1
                if sock.sendto(wire, node.address) != len(wire):
                    raise OSError("partial broker datagram")

            def service(events: AccountEvents) -> None:
                nonlocal beats
                beats += sum(h.status == "response" for h in events.heartbeats)
                if events.retired is not None:
                    raise _Stop(events.retired)
                for wire in events.outgoing:
                    send(wire)

            while True:
                check()
                if self._drain.is_set():
                    service(self._dispatcher.drain())
                batch = self._dispatcher.step()
                service(batch.maintenance)
                for item in batch.reads:
                    check()
                    if self.mailbox.ready(item.ticket):
                        send(item.prepared.wire)
                sock.settimeout(min(0.05, max(0.001, deadline - self._clock())))
                try:
                    wire, peer = sock.recvfrom(4097)
                except TimeoutError:
                    continue
                received += 1
                check()
                complete: list[bytes] = []
                acknowledgements = maintenance.receive(wire, peer, inspect_message=complete.append)
                for plain in complete:
                    service(self._dispatcher.receive(plain, peer, generation=self._generation))
                for acknowledgement in acknowledgements:
                    send(acknowledgement)
        except _Stop as stopped:
            reason = stopped.reason
        except OSError:
            reason = "socket"
        except ValueError:
            reason = "protocol"
        finally:
            try:
                self._dispatcher.close()
                if maintenance is not None:
                    maintenance.close()
                self._node = None
            finally:
                try:
                    sock.close()
                except OSError:
                    close_failed = True
                finally:
                    with self._claim:
                        self._socket = None
        return WorkerSummary(reason, sent, received, beats, close_failed)
