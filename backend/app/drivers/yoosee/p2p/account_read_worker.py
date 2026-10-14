"""Finite exclusive encrypted socket pump; concurrent callers use only its mailbox."""

import struct
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass

from .access_protocol import build_mode2_response_ack
from .account_output_budget import AccountOutputBudget, OutputExhausted
from .account_read_dispatcher import AccountReadDispatcher
from .broker_account import AccountEvents
from .broker_maintenance import BrokerMaintenance
from .broker_read_socket import ReadSocket
from .contracts import CertifiedNode
from .ptz_inbox import MotionTicket, PtzInbox
from .ptz_scheduler import PtzScheduler, PtzSpec, ScheduledPtzResult
from .ptz_socket_pump import pump_ptz


@dataclass(frozen=True, slots=True)
class WorkerSummary:
    reason: str
    sent_datagrams: int
    received_datagrams: int
    heartbeat_responses: int
    socket_close_failed: bool
    motion: ScheduledPtzResult | None = None


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
                 clock: Callable[[], float] = time.monotonic, allow_motion: bool = False):
        if type(allow_motion) is not bool:
            raise ValueError("explicit motion admission flag required")
        self._dispatcher = AccountReadDispatcher(node, generation, local_ip, local_port, clock=clock)
        self.mailbox = self._dispatcher.mailbox
        self.motion_inbox = PtzInbox(generation, clock=clock) if allow_motion else None
        self._node: CertifiedNode | None = node
        self._generation, self._clock = generation, clock
        self._stop = threading.Event()
        self._drain = threading.Event()
        self._claim = threading.Lock()
        self._started = False
        self._socket: ReadSocket | None = None

    def stop(self) -> None:
        if self.motion_inbox is not None:
            self.motion_inbox.close()
        self._stop.set()

    def drain(self) -> None:
        """Request no new work, with <=2 seconds for already active replies."""
        if self.motion_inbox is not None:
            self.motion_inbox.close()
        self._drain.set()

    def run(self, sock: ReadSocket, *, cancelled: Callable[[], bool] = lambda: False,
            initial_motion: PtzSpec | None = None,
            wait: Callable[[float], None] = time.sleep) -> WorkerSummary:
        """Optional trusted bootstrap gesture; no public/concurrent PTZ admission.

        Motion is driven by this same reader. Driver must authorize the spec.
        Every shutdown path drains its STOP before physically closing the socket.
        """
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
        motion: PtzScheduler | None = None
        motion_ticket: MotionTicket | None = None
        last_motion: ScheduledPtzResult | None = None
        mixed = initial_motion is not None or self.motion_inbox is not None
        node = self._node
        try:
            assert node is not None
            deadline = self._clock() + 60
            maintenance = BrokerMaintenance.account_check(node, clock=self._clock,
                acknowledge_messages=not mixed)
            if initial_motion is not None:
                if not isinstance(initial_motion, PtzSpec) or self.motion_inbox is not None:
                    raise ValueError("typed motion specification required")
                motion = self._dispatcher.prepare_ptz(initial_motion, output)

            def check() -> None:
                if self._stop.is_set() or cancelled():
                    raise _Stop("stopped")
                if self._clock() >= deadline:
                    raise _Stop("deadline")

            def send(wire: bytes, *, reserved: bool = False) -> None:
                nonlocal sent
                check()
                try:
                    if not reserved:
                        output.charge(len(wire))
                except OutputExhausted:
                    raise _Stop("output_budget") from None
                sock.settimeout(min(0.02 if mixed else 0.2, max(0.001, deadline - self._clock())))
                sent += 1
                try:
                    if sock.sendto(wire, node.address) != len(wire):
                        raise OSError("partial broker datagram")
                except BaseException:
                    if reserved and motion is not None:
                        motion.send_failed()
                    raise

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
                if self.motion_inbox is not None:
                    if motion is not None and motion.result is not None:
                        assert motion_ticket is not None
                        last_motion = motion.result
                        self.motion_inbox.finish(motion_ticket, last_motion)
                        motion, motion_ticket = None, None
                    if motion is None:
                        claimed = self.motion_inbox.claim()
                        if claimed is not None:
                            motion_ticket, spec = claimed
                            try:
                                motion = self._dispatcher.prepare_ptz(spec, output)
                            except BaseException:
                                self.motion_inbox.finish(motion_ticket,
                                    ScheduledPtzResult("cancelled", False, 0, False, False))
                                raise
                    if motion is not None and motion_ticket is not None:
                        if self.motion_inbox.should_cancel(motion_ticket):
                            motion.cancel()
                if motion is not None:
                    packet = motion.next_packet()
                    if packet is not None:
                        send(packet.wire, reserved=True)
                batch = self._dispatcher.step()
                service(batch.maintenance)
                for item in batch.reads:
                    check()
                    if self.mailbox.ready(item.ticket):
                        send(item.prepared.wire)
                sock.settimeout(min(0.02 if mixed else 0.05, max(0.001, deadline - self._clock())))
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
                    if motion is not None:
                        for packet in motion.observe(plain, peer, generation=self._generation):
                            send(packet.wire, reserved=True)
                    if mixed:
                        flags = struct.unpack_from("<I", plain, 20)[0]
                        if ((motion is None or not motion.owns_release_reply(plain))
                                and not flags & (1 << 20) and (flags >> 18) & 3):
                            send(build_mode2_response_ack(node, plain))
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
                if self.motion_inbox is not None:
                    self.motion_inbox.close()
                self._dispatcher.close()
                if motion is not None and motion.result is None:
                    assert node is not None
                    motion.cancel()
                    try:
                        cleanup = pump_ptz(sock, node, motion, output, generation=self._generation,
                                           cancelled=lambda: True, clock=self._clock, wait=wait)
                        sent += cleanup.sent_datagrams
                        received += cleanup.received_datagrams
                    finally:
                        if maintenance is not None:
                            maintenance.close()
                        self._node = None
                if maintenance is not None:
                    maintenance.close()
                self._node = None
            finally:
                if (self.motion_inbox is not None and motion_ticket is not None
                        and motion is not None and motion.result is not None):
                    self.motion_inbox.finish(motion_ticket, motion.result)
                try:
                    sock.close()
                except OSError:
                    close_failed = True
                finally:
                    with self._claim:
                        self._socket = None
        return WorkerSummary(reason, sent, received, beats, close_failed,
                             motion.result if motion is not None else last_motion)
