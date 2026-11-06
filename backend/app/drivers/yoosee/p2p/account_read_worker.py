"""Exclusive encrypted account pump; concurrent callers use only its mailbox."""

import struct
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass

from .access_protocol import build_mode2_response_ack
from .account_lan_probe import AccountLanProbe
from .account_media_channel import (
    AccountMediaChannel,
    MediaBootstrap,
    MediaChannelSummary,
    media_channels_closed,
)
from .account_output_budget import AccountOutputBudget, OutputExhausted
from .account_policy import AccountPolicy, validate_policy
from .account_read_dispatcher import AccountReadDispatcher
from .broker_account import AccountEvents
from .broker_maintenance import BrokerMaintenance
from .broker_read_socket import ReadSocket
from .catalog_read_inbox import CatalogReadInbox
from .catalog_read_pump import CatalogReadPump
from .contracts import CertifiedNode
from .cooperative_tcp_media import CooperativeTcpMedia, TcpMediaResult, open_tcp_media
from .kcp_receive import KcpAssemblyTimeout
from .lamp_read_inbox import LampReadInbox
from .lamp_read_pump import LampReadPump
from .media_failures import PROTOCOL_CODES, underlying_receive_error
from .media_lease_inbox import MediaLeaseInbox
from .media_lease_pump import MediaLeasePump
from .media_policy import MediaPolicy, validate_media_policy
from .media_route_cleanup import close_media_route
from .media_route_job import MediaRouteJob, MediaRouteResult, MediaRouteSpec
from .model_control_inbox import ControlWriteInbox
from .model_control_pump import ControlWritePump
from .mtp_tcp_framing import MtpTcpFrameError
from .ptz_inbox import MotionTicket, PtzInbox
from .ptz_scheduler import PtzScheduler, PtzSpec, ScheduledPtzResult
from .ptz_socket_pump import pump_ptz
from .siren_pulse_inbox import SirenPulseInbox
from .siren_pulse_job import SirenPulseResult
from .siren_pulse_pump import SirenPulsePump
from .siren_socket_cleanup import drain_siren
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
    siren: SirenPulseResult | None = None
    protocol_facts: tuple[tuple[str, int], ...] = ()
    media_channels: tuple[MediaChannelSummary, ...] = ()
    lease_cleanup_uncertain: bool = False


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
                 policy: AccountPolicy = AccountPolicy.FINITE, state_reads: bool = False,
                 media_capacity: int = 1):
        validate_policy(policy)
        self._policy = policy
        if type(allow_motion) is not bool:
            raise ValueError("explicit motion admission flag required")
        self._dispatcher = AccountReadDispatcher(node, generation, local_ip, local_port,
            clock=clock, policy=policy, state_reads=state_reads, media_capacity=media_capacity)
        self._media_capacity = media_capacity
        self.mailbox = self._dispatcher.mailbox
        self.lamp_inbox = LampReadInbox(generation, clock=clock) if state_reads else None
        self.catalog_inbox = CatalogReadInbox(generation, clock=clock) if state_reads else None
        self.control_inbox = ControlWriteInbox(generation, clock=clock) if state_reads else None
        self.siren_inbox = SirenPulseInbox(generation, clock=clock) if state_reads else None
        self.motion_inbox = PtzInbox(generation, clock=clock) if allow_motion else None
        self._node: CertifiedNode | None = node
        self._generation, self._clock = generation, clock
        self._stop = threading.Event()
        self._drain = threading.Event()
        self._claim = threading.Lock()
        self._started = False
        self._socket: ReadSocket | None = None
        self._media_leases: MediaLeaseInbox | None = None

    def stop(self) -> None:
        if self._media_leases is not None:
            self._media_leases.close()
        if self.siren_inbox is not None:
            self.siren_inbox.close()
        if self.catalog_inbox is not None:
            self.catalog_inbox.close()
        if self.control_inbox is not None:
            self.control_inbox.close()
        if self.lamp_inbox is not None:
            self.lamp_inbox.close()
        if self.motion_inbox is not None:
            self.motion_inbox.close()
        self._stop.set()

    def drain(self) -> None:
        """Request no new work, with <=2 seconds for already active replies."""
        if self._media_leases is not None:
            self._media_leases.close()
        if self.siren_inbox is not None:
            self.siren_inbox.close()
        if self.catalog_inbox is not None:
            self.catalog_inbox.close()
        if self.motion_inbox is not None:
            self.motion_inbox.close()
        if self.lamp_inbox is not None:
            self.lamp_inbox.close()
        if self.control_inbox is not None:
            self.control_inbox.close()
        self._drain.set()

    def run(self, sock: ReadSocket, *, cancelled: Callable[[], bool] = lambda: False,
            initial_motion: PtzSpec | None = None,
            initial_media: MediaRouteSpec | None = None,
            additional_media: tuple[MediaBootstrap, ...] = (),
            media_leases: MediaLeaseInbox | None = None,
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
        output = AccountOutputBudget(policy=self._policy, clock=self._clock,
                                     media_capacity=self._media_capacity)
        reason = "stopped"
        protocol_site = ""
        protocol_code = ""
        protocol_facts: tuple[tuple[str, int], ...] = ()
        close_failed = False
        maintenance = None
        motion: PtzScheduler | None = None
        motion_device_id: int | None = None
        media: MediaRouteJob | None = None
        tcp: CooperativeTcpMedia | None = None
        channels: list[AccountMediaChannel] = []
        leases: MediaLeasePump | None = None
        lan = AccountLanProbe(clock=self._clock)
        unexpected_exit = True
        tcp_cleanup_error: BaseException | None = None
        media_cleanup_error: BaseException | None = None
        motion_cleanup_error: BaseException | None = None
        siren_cleanup_error: BaseException | None = None
        motion_ticket: MotionTicket | None = None
        last_motion: ScheduledPtzResult | None = None
        lamp = LampReadPump(self._dispatcher, self.lamp_inbox, clock=self._clock) if self.lamp_inbox is not None else None
        catalog = CatalogReadPump(self._dispatcher, self.catalog_inbox) if self.catalog_inbox is not None else None
        control_writes = ControlWritePump(self._dispatcher, self.control_inbox, clock=self._clock) if self.control_inbox is not None else None
        siren = SirenPulsePump(self._dispatcher, self.siren_inbox, output) if self.siren_inbox is not None else None
        mixed = initial_motion is not None or self.motion_inbox is not None
        manual_acks = mixed or lamp is not None
        node = self._node
        try:
            assert node is not None
            validate_media_policy(media_policy)
            if type(allow_media_motion) is not bool:
                raise ValueError("explicit media/motion admission required")
            if allow_media_motion and ((initial_media is None and media_leases is None) or initial_motion is not None
                    or self.motion_inbox is None or not with_media_tcp
                    or media_policy is not MediaPolicy.CONTINUOUS
                    or self._policy is not AccountPolicy.CONTINUOUS):
                raise ValueError("reviewed continuous media/motion scope required")
            if media_policy is MediaPolicy.CONTINUOUS and (
                    not with_media_tcp or self._policy is not AccountPolicy.CONTINUOUS):
                raise ValueError("continuous media requires a continuous account and TCP")
            if type(with_media_tcp) is not bool or (with_media_tcp and initial_media is None and media_leases is None):
                raise ValueError("TCP media requires explicit typed route bootstrap")
            if media_leases is not None:
                if (not isinstance(media_leases, MediaLeaseInbox)
                        or media_leases.generation != self._generation
                        or media_leases.capacity > self._media_capacity
                        or initial_media is not None or additional_media or initial_motion is not None
                        or video_delivery is not None or (self.motion_inbox is not None and not allow_media_motion)
                        or not with_media_tcp or media_policy is not MediaPolicy.CONTINUOUS
                        or self._policy is not AccountPolicy.CONTINUOUS):
                    raise ValueError("exclusive reviewed continuous source leases required")
                self._media_leases = media_leases
                leases = MediaLeasePump(media_leases, channels, clock=self._clock)
            if video_delivery is not None and (not isinstance(video_delivery, VideoDelivery)
                                               or not with_media_tcp or video_delivery.closed):
                raise ValueError("video delivery requires a fresh TCP media handoff")
            if (not isinstance(additional_media, tuple)
                    or len(additional_media) >= self._media_capacity
                    or any(not isinstance(item, MediaBootstrap)
                           or not isinstance(item.spec, MediaRouteSpec) for item in additional_media)):
                raise ValueError("bounded typed additional media required")
            if additional_media and (initial_media is None or not with_media_tcp
                    or media_policy is not MediaPolicy.CONTINUOUS
                    or self._policy is not AccountPolicy.CONTINUOUS):
                raise ValueError("multiple media requires explicit continuous account and TCP")
            deliveries = ([video_delivery] if video_delivery is not None else [])
            for bootstrap in additional_media:
                if bootstrap.video is not None:
                    if (not isinstance(bootstrap.video, VideoDelivery) or bootstrap.video.closed
                            or any(bootstrap.video is other for other in deliveries)):
                        raise ValueError("independent fresh media deliveries required")
                    deliveries.append(bootstrap.video)
            deadline = self._clock() + 60 if self._policy is AccountPolicy.FINITE else float("inf")
            maintenance = BrokerMaintenance.account_check(node, clock=self._clock,
                acknowledge_messages=not manual_acks, policy=self._policy)
            if initial_media is not None:
                if not isinstance(initial_media, MediaRouteSpec) or (mixed and not allow_media_motion):
                    raise ValueError("typed media-only bootstrap required")
                media = self._dispatcher.prepare_media(initial_media, output)
                channels.append(AccountMediaChannel(MediaBootstrap(initial_media, video_delivery),
                    media, clock=self._clock, policy=media_policy))
                for bootstrap in additional_media:
                    route = self._dispatcher.prepare_media(bootstrap.spec, output)
                    channels.append(AccountMediaChannel(bootstrap, route, clock=self._clock, policy=media_policy))
            if initial_motion is not None:
                if not isinstance(initial_motion, PtzSpec) or self.motion_inbox is not None:
                    raise ValueError("typed motion specification required")
                motion = self._dispatcher.prepare_ptz(initial_motion, output)
                motion_device_id = initial_motion.device_id

            def check() -> None:
                if self._stop.is_set() or cancelled():
                    raise _Stop("stopped")
                if self._clock() >= deadline:
                    raise _Stop("deadline")

            def send(wire: bytes, *, reserved: bool = False, peer: tuple[str, int] | None = None) -> None:
                nonlocal sent
                check()
                try:
                    if not reserved:
                        output.charge(len(wire))
                except OutputExhausted:
                    if peer is not None:
                        return  # An optional LAN measurement cannot spend cleanup credit.
                    raise _Stop("output_budget") from None
                sock.settimeout(min(0.02 if mixed else 0.2, max(0.001, deadline - self._clock())))
                sent += 1
                try:
                    if sock.sendto(wire, node.address if peer is None else peer) != len(wire):
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
                if leases is not None:
                    if (motion is not None and motion.result is not None and motion.result.start_attempted
                            and not motion.result.release_delivery_confirmed):
                        raise _Stop("transport")
                    moving = motion_device_id if motion is not None and motion.result is None else None
                    if moving is not None and motion is not None and not leases.motion_ready(moving):
                        motion.cancel()
                    leases.poll(lambda route_spec: self._dispatcher.prepare_media(route_spec, output),
                                defer_stop_device=moving)
                for channel in channels:
                    if channel.route.result is not None:
                        continue
                    if with_media_tcp:
                        try:
                            channel.step(open_tcp_media, budget_seconds=0.2 / len(channels))
                        except (OSError, ValueError) as channel_error:
                            if len(channels) == 1 and leases is None:
                                raise
                            # The TCP opener/pump certifies physical close or
                            # raises a distinct cleanup error. Never reopen here.
                            cause = underlying_receive_error(channel_error)
                            channel.retire("socket" if isinstance(channel_error, OSError) else "protocol",
                                protocol_code=PROTOCOL_CODES.get(str(cause), ""),
                                protocol_facts=cause.facts if isinstance(
                                    cause, (KcpAssemblyTimeout, MtpTcpFrameError)) else ())
                        if channel is channels[0]:
                            tcp = channel.tcp
                        if channel.tcp is not None and channel.tcp.result is not None:
                            completion = ("media_complete" if channel.tcp.result.close_acknowledged
                                        and not channel.tcp.result.socket_close_failed
                                        else "media_unconfirmed")
                            if len(channels) == 1 and leases is None:
                                raise _Stop(completion)
                            if channel.tcp.result.socket_close_failed:
                                raise _Stop("media_unconfirmed")
                            channel.retire("ended")
                    defer_close = (leases is not None and motion is not None and motion.result is None
                                   and motion_device_id == channel.bootstrap.spec.device.device_id)
                    packet_media = None if defer_close else channel.route.next_packet()
                    if packet_media is not None:
                        try:
                            send(packet_media.wire, reserved=True)
                        except BaseException:
                            channel.route.send_failed()
                            raise
                if leases is not None:
                    for wire_lan, peer_lan in lan.poll(tuple(channel.route for channel in channels)):
                        # Best-effort measurement must not retire a healthy TCP
                        # stream on LAN-specific routing/firewall send failure.
                        try:
                            send(wire_lan, peer=peer_lan)
                        except OSError:
                            pass
                if leases is None and len(channels) > 1 and all(channel.route.result is not None for channel in channels):
                    closed = media_channels_closed(tuple(channel.summary() for channel in channels))
                    raise _Stop("media_complete" if closed else "media_unconfirmed")
                if self.motion_inbox is not None:
                    if motion is not None and motion.result is not None:
                        assert motion_ticket is not None
                        last_motion = motion.result
                        self.motion_inbox.finish(motion_ticket, last_motion)
                        motion, motion_ticket, motion_device_id = None, None, None
                    ready = leases.any_motion_ready() if leases is not None else (media is None or all(
                            channel.tcp is not None and channel.tcp.ready for channel in channels))
                    if (motion is None and ready
                            and (siren is None or (not siren.active and not siren.unsafe))):
                        claimed = self.motion_inbox.claim()
                        if claimed is not None:
                            motion_ticket, spec = claimed
                            if leases is not None and not leases.motion_ready(spec.device_id):
                                self.motion_inbox.finish(motion_ticket,
                                    ScheduledPtzResult("cancelled", False, 0, False, False))
                                motion_ticket = None
                            else:
                                try:
                                    motion = self._dispatcher.prepare_ptz(spec, output)
                                    motion_device_id = spec.device_id
                                except BaseException:
                                    self.motion_inbox.finish(motion_ticket,
                                        ScheduledPtzResult("cancelled", False, 0, False, False))
                                    raise
                    if motion is not None and motion_ticket is not None:
                        if self.motion_inbox.should_cancel(motion_ticket):
                            motion.cancel()
                if motion is not None:
                    if leases is not None and motion_device_id is not None and not leases.motion_ready(motion_device_id):
                        motion.cancel()
                    packet = motion.next_packet()
                    if packet is not None:
                        send(packet.wire, reserved=True)
                batch = self._dispatcher.step()
                service(batch.maintenance)
                if lamp is not None:
                    for packet_lamp in lamp.poll():
                        send(packet_lamp)
                if catalog is not None:
                    for packet_catalog in catalog.poll():
                        send(packet_catalog)
                if control_writes is not None:
                    for packet_control in control_writes.poll():
                        send(packet_control)
                if siren is not None:
                    for packet_siren in siren.poll():
                        try:
                            send(packet_siren.wire, reserved=True)
                        except BaseException:
                            siren.send_failed()
                            raise
                    if siren.unsafe:
                        raise _Stop('siren_cleanup_unconfirmed')
                for item in batch.reads:
                    check()
                    if self.mailbox.ready(item.ticket):
                        send(item.prepared.wire)
                # A 50ms idle UDP wait would cap the 4096-byte TCP pump near
                # 80KiB/s and accumulate video latency despite a healthy relay.
                interval = 0.001 if any(channel.tcp is not None for channel in channels) else 0.02 if mixed else 0.05
                sock.settimeout(min(interval, max(0.001, deadline - self._clock())))
                try:
                    wire, peer = sock.recvfrom(4097)
                except TimeoutError:
                    continue
                received += 1
                check()
                if peer != node.address:
                    lan.receive(wire, peer)
                complete: list[bytes] = []
                acknowledgements = maintenance.receive(wire, peer, inspect_message=complete.append)
                for plain in complete:
                    siren_packets = siren.observe(plain, peer, generation=self._generation) if siren is not None else ()
                    for packet_siren in siren_packets:
                        send(packet_siren.wire, reserved=True)
                    if catalog is not None:
                        catalog.observe(plain, peer, generation=self._generation)
                    if control_writes is not None:
                        control_writes.observe(plain, peer, generation=self._generation)
                    lamp_packets = lamp.observe(plain, peer, generation=self._generation) if lamp is not None else ()
                    for packet_lamp in lamp_packets:
                        send(packet_lamp)
                    service(self._dispatcher.receive(plain, peer, generation=self._generation))
                    if motion is not None:
                        for packet in motion.observe(plain, peer, generation=self._generation):
                            send(packet.wire, reserved=True)
                    if manual_acks:
                        flags = struct.unpack_from("<I", plain, 20)[0]
                        if ((motion is None or not motion.owns_release_reply(plain)) and not lamp_packets and not siren_packets
                                and not flags & (1 << 20) and (flags >> 18) & 3):
                            send(build_mode2_response_ack(node, plain))
                for acknowledgement in acknowledgements:
                    send(acknowledgement)
        except _Stop as stopped:
            reason = stopped.reason
            unexpected_exit = False
        except OSError:
            reason = "socket"
            unexpected_exit = False
        except ValueError as error:
            reason = "protocol"
            # TCP/AV boundaries sanitize their public error messages. Retain the
            # underlying fixed classification rather than treating a wrapped KCP
            # timeout as an unknown protocol failure. Never log exception text.
            for _ in range(8):
                if not isinstance(error.__context__, ValueError):
                    break
                error = error.__context__
            trace=error.__traceback__
            sites=[]
            while trace is not None:
                sites.append(f"{trace.tb_frame.f_code.co_name}:{trace.tb_lineno}")
                trace=trace.tb_next
            protocol_site='/'.join(sites[-2:])
            protocol_code=PROTOCOL_CODES.get(str(error),'')
            if isinstance(error, (KcpAssemblyTimeout, MtpTcpFrameError)):
                protocol_facts = error.facts
            unexpected_exit = False
        finally:
            lan.close()
            try:
                if siren is not None:
                    assert node is not None
                    try:
                        cleanup_sent, cleanup_received = drain_siren(sock, node, siren, output,
                            generation=self._generation, clock=self._clock, wait=wait)
                        sent += cleanup_sent
                        received += cleanup_received
                    except BaseException as exc:
                        siren_cleanup_error = exc
                if catalog is not None:
                    catalog.close()
                if control_writes is not None:
                    control_writes.close()
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
                for channel in channels:
                    try:
                        channel.finish_tcp()
                    except BaseException as exc:
                        if tcp_cleanup_error is None:
                            tcp_cleanup_error = exc
                self._dispatcher.close()
                try:
                    for channel in channels:
                        if channel.route.result is not None:
                            continue
                        assert node is not None
                        try:
                            media_sent, media_received = close_media_route(sock, node, channel.route, output,
                                generation=self._generation, clock=self._clock, wait=wait)
                            sent += media_sent
                            received += media_received
                        except BaseException as exc:
                            if media_cleanup_error is None:
                                media_cleanup_error = exc
                finally:
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
                    if leases is not None:
                        final_motion = motion.result if motion is not None else last_motion
                        motion_uncertain = (final_motion is not None and final_motion.start_attempted
                                            and not final_motion.release_delivery_confirmed)
                        leases.finish(uncertain=unexpected_exit or close_failed or tcp_cleanup_error is not None
                            or media_cleanup_error is not None or motion_cleanup_error is not None
                            or motion_uncertain or siren_cleanup_error is not None or (siren is not None and siren.unsafe))
                    if isinstance(video_delivery, VideoDelivery):
                        video_delivery.close()
                    with self._claim:
                        self._socket = None
                    if motion_cleanup_error is not None:
                        raise motion_cleanup_error
                    if siren_cleanup_error is not None:
                        raise siren_cleanup_error
                    if tcp_cleanup_error is not None:
                        raise tcp_cleanup_error
                    if media_cleanup_error is not None:
                        raise media_cleanup_error
        if channels:
            tcp = channels[0].tcp
        return WorkerSummary(reason, sent, received, beats, close_failed,
                             motion.result if motion is not None else last_motion,
                             media.result if media is not None else None,
                             tcp.result if tcp is not None else None,protocol_site,protocol_code,
                             siren.last_result if siren is not None else None, protocol_facts,
                             tuple(channel.summary() for channel in channels),
                             leases.cleanup_uncertain if leases is not None else False)
