"""LAN AV on the existing account reader; no sockets, threads or extra login."""

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass

from .av_handshake import AvHandshake
from .av_meter import AvMeter
from .media_policy import MediaPolicy, MediaRateBudget
from .media_protocol import build_media_meter_request, parse_kcp_segments, parse_media_meter
from .media_route_job import MediaRouteJob, MediaRouteSpec
from .mtp_udp_kcp import normalize_udp_kcp
from .video_delivery import VideoDelivery

log = logging.getLogger(__name__)
UdpSend = Callable[[bytes, tuple[str, int]], None]


@dataclass(frozen=True, slots=True)
class UdpMediaResult:
    ready: bool
    close_acknowledged: bool
    detached: bool
    video_frames: int
    audio_frames: int
    encoding_headers: int


class CooperativeUdpMedia:
    """One negotiated LAN route, one AV engine and immediate bounded delivery.

    step/receive run only on the account thread. Cancellation drains CLOSE in
    normal reader turns; shutdown finish sends a best-effort CLOSE then detaches.
    The account must still obtain its broker route release before replacement.
    Detachment is deliberately NOT reported as closing the shared UDP socket.
    """

    def __init__(self, route: MediaRouteJob, spec: MediaRouteSpec, send: UdpSend, *,
                 clock: Callable[[], float] = time.monotonic, video: VideoDelivery | None = None):
        if route.lan_endpoint is None or not route.lan_roundtrip_confirmed:
            raise ValueError("UDP AV requires a measured LAN route")
        self.peer = route.lan_endpoint
        self._clock, self._send, self._delivery = clock, send, video
        self._link, self._call = route.attempt.link_id, route.attempt.call_id
        self._access, self._device = spec.access_id, spec.device.device_id
        self._owner = AvHandshake(self.peer, self._link, self._call, route.attempt.cookie,
            request_user_data=route.metadata, policy=MediaPolicy.CONTINUOUS, clock=clock)
        self._meter = AvMeter(self.peer, self._link, self._call, self._access, self._device,
                              continuous=True)
        self._traffic = MediaRateBudget(clock=clock)
        self._next_meter = clock() + 1
        self._sequence = 2  # Initial route measurements used sequences1/2.
        self._close_until: float | None = None
        self.was_ready = False
        self._video = self._audio = self._headers = 0
        self.result: UdpMediaResult | None = None

    @property
    def ready(self) -> bool:
        return self.result is None and self._owner.ready and self._close_until is None

    def _write(self, wire: bytes) -> None:
        self._traffic.charge(tx=len(wire))
        self._send(wire, self.peer)

    def _close(self) -> UdpMediaResult:
        if self.result is None:
            confirmed = self._owner.close_acknowledged
            self._owner.close()
            if self._delivery is not None:
                self._delivery.close()
            self.result = UdpMediaResult(self.was_ready, confirmed, True,
                                         self._video, self._audio, self._headers)
        return self.result

    def step(self) -> None:
        if self.result is not None:
            return
        try:
            if self._close_until is not None and self._clock() >= self._close_until:
                self._close()
                return
            for wire in self._owner.due():
                self._write(wire)
            if self._close_until is None and self._clock() >= self._next_meter:
                self._sequence = (self._sequence + 1) & 0xFFFFFFFF
                self._next_meter = self._clock() + 1
                self._write(build_media_meter_request(self._access, self._device, self._link,
                    self._call, sequence=self._sequence, timestamp=int(self._clock() * 1000) & 0xFFFFFFFF))
        except BaseException:
            self._close()
            raise

    def receive(self, wire: bytes, peer: tuple[str, int]) -> bool:
        """Only validated exact-route traffic leaves the broker's foreign budget."""
        if self.result is not None or peer != self.peer:
            return False
        meter = parse_media_meter(wire) if len(wire) in (74, 78) else None
        if meter is not None:
            if (meter.link_id != self._link or meter.source_id != self._device
                    or meter.destination_id != self._access or meter.channel_type != 4
                    or meter.record_length != len(wire) - 6 or meter.kind not in (1, 2)):
                return False
        else:
            try:
                wire = normalize_udp_kcp(wire)
                segments = parse_kcp_segments(wire)
            except ValueError:
                return False
            if not segments or any(item.conv not in (self._link, self._link | 0x80000000)
                                   for item in segments):
                return False
        try:
            self._traffic.charge(rx=len(wire), records=1)
            if meter is not None:
                reply = self._meter.receive(wire, peer)
                if reply is not None:
                    self._write(reply)
                return True
            batch = self._owner.receive(wire, peer)
            for reply in batch.acknowledgements:
                self._write(reply)
            for record in batch.records:
                if self._delivery is not None:
                    self._delivery.consume(record)
                self._video += bool(record.video)
                self._audio += len(record.audio)
                self._headers += record.encoding is not None
            if self._owner.ready and not self.was_ready:
                self.was_ready = True
                log.info("native UDP AV ready transport=lan")
            if self._owner.close_acknowledged:
                self._close()
            return True
        except BaseException:
            self._close()
            raise

    def begin_finish(self) -> None:
        if self.result is not None or self._close_until is not None:
            return
        if not self.ready:
            self._close()
            return
        try:
            self._owner.begin_finish()
            self._close_until = self._clock() + 2
        except BaseException:
            self._close()
            raise

    def finish(self) -> UdpMediaResult:
        """No nested recvfrom: broker release follows this local AV detachment."""
        try:
            self.begin_finish()
            self.step()
        except (OSError, ValueError):
            pass
        return self._close()
