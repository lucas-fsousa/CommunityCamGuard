"""Exclusive encrypted account pump; concurrent callers use only its mailbox."""

import struct
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass

from .access_protocol import build_mode2_response_ack
from .account_output_budget import AccountOutputBudget, OutputExhausted
from .account_policy import AccountPolicy, validate_policy
from .account_read_dispatcher import AccountReadDispatcher
from .broker_account import AccountEvents
from .broker_maintenance import BrokerMaintenance
from .broker_read_socket import ReadSocket
from .contracts import CertifiedNode
from .cooperative_tcp_media import CooperativeTcpMedia, TcpMediaResult, open_tcp_media
from .lamp_read_inbox import LampReadInbox
from .lamp_read_pump import LampReadPump
from .media_policy import MediaPolicy, validate_media_policy
from .media_route_cleanup import close_media_route
from .media_route_job import MediaRouteJob, MediaRouteResult, MediaRouteSpec
from .ptz_inbox import MotionTicket, PtzInbox
from .ptz_scheduler import PtzScheduler, PtzSpec, ScheduledPtzResult
from .ptz_socket_pump import pump_ptz
from .video_delivery import VideoDelivery


@dataclass(frozen=True, slots=True)
class WorkerSummary:
    reason: str
    sent_datagrams: int
    received_datagrams: int
    heartbeat_responses: int
    socket_close_failed: bool
    motion: ScheduledPtzResult | None = None
    media: MediaRouteResult | None = None
    tcp: TcpMediaResult | None = None
    protocol_site: str = ""
    protocol_code: str = ""


class _Stop(Exception):
    def __init__(self, reason: str):
        self.reason = reason


class AccountReadWorker:
    """Run exactly once with a transferred socket, no authentication/retries.

    Default finite policy retains the60-second diagnostic. Explicit continuous
    policy lives until cancellation, drain, heartbeat failure or a safety limit;
    traffic allowances replenish but queued work and cleanup stay bounded.

    Creates no thread. The caller retains enrollment and exclusive shared-account
    reservations until run returns. Never supply a socket borrowed from PTZ/cache.
    submit/cancel/take through mailbox and stop are safe from other caller threads.
    """

    def __init__(self, node: CertifiedNode, generation: int, local_ip: str, local_port: int, *,
                 clock: Callable[[], float] = time.monotonic, allow_motion: bool = False,
                 policy: AccountPolicy = AccountPolicy.FINITE, state_reads: bool = False):
        validate_policy(policy)
        self._policy = policy
        if type(allow_motion) is not bool:
            raise ValueError("explicit motion admission flag required")
        self._dispatcher = AccountReadDispatcher(node, generation, local_ip, local_port,
                                                 clock=clock, policy=policy, state_reads=state_reads)
        self.mailbox = self._dispatcher.mailbox
        self.lamp_inbox = LampReadInbox(generation, clock=clock) if state_reads else None
        self.motion_inbox = PtzInbox(generation, clock=clock) if allow_motion else None
        self._node: CertifiedNode | None = node
        self._generation, self._clock = generation, clock
        self._stop = threading.Event()
        self._drain = threading.Event()
        self._claim = threading.Lock()
        self._started = False
        self._socket: ReadSocket | None = None

    def stop(self) -> None:
        if self.lamp_inbox is not None:
            self.lamp_inbox.close()
        if self.motion_inbox is not None:
            self.motion_inbox.close()
        self._stop.set()

    def drain(self) -> None:
        """Request no new work, with <=2 seconds for already active replies."""
        if self.motion_inbox is not None:
            self.motion_inbox.close()
        if self.lamp_inbox is not None:
            self.lamp_inbox.close()
        self._drain.set()

    def run(self, sock: ReadSocket, *, cancelled: Callable[[], bool] = lambda: False,
            initial_motion: PtzSpec | None = None,
            initial_media: MediaRouteSpec | None = None,
            with_media_tcp: bool = False,
            video_delivery: VideoDelivery | None = None,
            media_policy: MediaPolicy = MediaPolicy.FINITE,
            allow_media_motion: bool = False,
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
        output = AccountOutputBudget(policy=self._policy, clock=self._clock)
        reason = "stopped"
        protocol_site = ""
        protocol_code = ""
        close_failed = False
        maintenance = None
        motion: PtzScheduler | None = None
        media: MediaRouteJob | None = None
        tcp: CooperativeTcpMedia | None = None
        tcp_cleanup_error: BaseException | None = None
        motion_cleanup_error: BaseException | None = None
        motion_ticket: MotionTicket | None = None
        last_motion: ScheduledPtzResult | None = None
        lamp = LampReadPump(self._dispatcher, self.lamp_inbox, clock=self._clock) if self.lamp_inbox is not None else None
        mixed = initial_motion is not None or self.motion_inbox is not None
        manual_acks = mixed or lamp is not None
        node = self._node
        try:
            assert node is not None
            validate_media_policy(media_policy)
            if type(allow_media_motion) is not bool:
                raise ValueError("explicit media/motion admission required")
            if allow_media_motion and (initial_media is None or initial_motion is not None
                    or self.motion_inbox is None or not with_media_tcp
                    or media_policy is not MediaPolicy.CONTINUOUS
                    or self._policy is not AccountPolicy.CONTINUOUS):
                raise ValueError("reviewed continuous media/motion scope required")
            if media_policy is MediaPolicy.CONTINUOUS and (
                    not with_media_tcp or self._policy is not AccountPolicy.CONTINUOUS):
                raise ValueError("continuous media requires a continuous account and TCP")
            if type(with_media_tcp) is not bool or (with_media_tcp and initial_media is None):
                raise ValueError("TCP media requires explicit typed route bootstrap")
            if video_delivery is not None and (not isinstance(video_delivery, VideoDelivery)
                                               or not with_media_tcp or video_delivery.closed):
                raise ValueError("video delivery requires a fresh TCP media handoff")
            deadline = self._clock() + 60 if self._policy is AccountPolicy.FINITE else float("inf")
            maintenance = BrokerMaintenance.account_check(node, clock=self._clock,
                acknowledge_messages=not manual_acks, policy=self._policy)
            if initial_media is not None:
                if not isinstance(initial_media, MediaRouteSpec) or (mixed and not allow_media_motion):
                    raise ValueError("typed media-only bootstrap required")
                media = self._dispatcher.prepare_media(initial_media, output)
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
                    if tcp is not None:
                        raise _Stop("draining")
                    service(self._dispatcher.drain())
                if media is not None:
                    if with_media_tcp and media.endpoint is not None and tcp is None:
                        assert initial_media is not None
                        if media_policy is MediaPolicy.CONTINUOUS:
                            tcp = open_tcp_media(media, initial_media, clock=self._clock,
                                                 video=video_delivery, policy=media_policy)
                        elif video_delivery is None:
                            tcp = open_tcp_media(media, initial_media, clock=self._clock)
                        else:
                            tcp = open_tcp_media(media, initial_media, clock=self._clock,
                                                 video=video_delivery)
                    if tcp is not None:
                        tcp.step()
                        if tcp.result is not None:
                            raise _Stop("media_complete" if tcp.result.close_acknowledged
                                        and not tcp.result.socket_close_failed
                                        else "media_unconfirmed")
                    packet_media = media.next_packet()
                    if packet_media is not None:
                        try:
                            send(packet_media.wire, reserved=True)
                        except BaseException:
                            media.send_failed()
                            raise
                if self.motion_inbox is not None:
                    if motion is not None and motion.result is not None:
                        assert motion_ticket is not None
                        last_motion = motion.result
                        self.motion_inbox.finish(motion_ticket, last_motion)
                        motion, motion_ticket = None, None
                    if motion is None and (media is None or (tcp is not None and tcp.ready)):
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
                if lamp is not None:
                    for packet_lamp in lamp.poll():
                        send(packet_lamp)
                for item in batch.reads:
                    check()
                    if self.mailbox.ready(item.ticket):
                        send(item.prepared.wire)
                # A 50ms idle UDP wait would cap the 4096-byte TCP pump near
                # 80KiB/s and accumulate video latency despite a healthy relay.
                interval = 0.001 if tcp is not None else 0.02 if mixed else 0.05
                sock.settimeout(min(interval, max(0.001, deadline - self._clock())))
                try:
                    wire, peer = sock.recvfrom(4097)
                except TimeoutError:
                    continue
                received += 1
                check()
                complete: list[bytes] = []
                acknowledgements = maintenance.receive(wire, peer, inspect_message=complete.append)
                for plain in complete:
                    lamp_packets = lamp.observe(plain, peer, generation=self._generation) if lamp is not None else ()
                    for packet_lamp in lamp_packets:
                        send(packet_lamp)
                    if media is not None:
                        media.observe(plain, peer, generation=self._generation)
                    service(self._dispatcher.receive(plain, peer, generation=self._generation))
                    if motion is not None:
                        for packet in motion.observe(plain, peer, generation=self._generation):
                            send(packet.wire, reserved=True)
                    if manual_acks:
                        flags = struct.unpack_from("<I", plain, 20)[0]
                        if ((motion is None or not motion.owns_release_reply(plain)) and not lamp_packets
                                and not flags & (1 << 20) and (flags >> 18) & 3):
                            send(build_mode2_response_ack(node, plain))
                for acknowledgement in acknowledgements:
                    send(acknowledgement)
        except _Stop as stopped:
            reason = stopped.reason
        except OSError:
            reason = "socket"
        except ValueError as error:
            reason = "protocol"
            trace=error.__traceback__
            sites=[]
            while trace is not None:
                sites.append(f"{trace.tb_frame.f_code.co_name}:{trace.tb_lineno}")
                trace=trace.tb_next
            protocol_site='/'.join(sites[-2:])
            protocol_code={
                'KCP assembly deadline exceeded':'kcp_assembly_timeout',
                'invalid KCP receive segment':'kcp_invalid_segment',
                'conflicting KCP duplicate':'kcp_conflicting_duplicate',
                'KCP receive budget exceeded':'kcp_receive_budget',
                'invalid KCP fragment chain':'kcp_fragment_chain',
            }.get(str(error),'')
        finally:
            try:
                if lamp is not None:
                    lamp.close()
                self.mailbox.close()
                if self.motion_inbox is not None:
                    self.motion_inbox.close()
                # Motor safety precedes potentially slow TCP/media teardown.
                # Keep the old broker socket and reserved STOP capacity throughout.
                if motion is not None and motion.result is None:
                    assert node is not None
                    motion.cancel()
                    try:
                        cleanup = pump_ptz(sock, node, motion, output, generation=self._generation,
                                           cancelled=lambda: True, clock=self._clock, wait=wait)
                        sent += cleanup.sent_datagrams
                        received += cleanup.received_datagrams
                    except BaseException as exc:
                        motion_cleanup_error = exc  # still retire TCP, route and socket
                if tcp is not None:
                    try:
                        tcp.finish()
                    except BaseException as exc:
                        tcp_cleanup_error = exc
                self._dispatcher.close()
                if media is not None and media.result is None:
                    assert node is not None
                    try:
                        media_sent, media_received = close_media_route(sock, node, media, output,
                            generation=self._generation, clock=self._clock, wait=wait)
                        sent += media_sent
                        received += media_received
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
                    if isinstance(video_delivery, VideoDelivery):
                        video_delivery.close()
                    with self._claim:
                        self._socket = None
                    if motion_cleanup_error is not None:
                        raise motion_cleanup_error
                    if tcp_cleanup_error is not None:
                        raise tcp_cleanup_error
        return WorkerSummary(reason, sent, received, beats, close_failed,
                             motion.result if motion is not None else last_motion,
                             media.result if media is not None else None,
                             tcp.result if tcp is not None else None,protocol_site,protocol_code)
