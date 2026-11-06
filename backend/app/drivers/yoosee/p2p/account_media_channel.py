"""One media transport under the account worker; never reads its broker socket."""

import time
from collections.abc import Callable
from dataclasses import dataclass, field

from .cooperative_tcp_media import CooperativeTcpMedia, TcpMediaResult
from .cooperative_udp_media import CooperativeUdpMedia, UdpMediaResult, UdpSend
from .media_policy import MediaPolicy
from .media_route_job import MediaRouteJob, MediaRouteResult, MediaRouteSpec
from .video_delivery import VideoDelivery


@dataclass(frozen=True, slots=True)
class MediaBootstrap:
    """Trusted internal admission, not a driver capability or public request."""
    spec: MediaRouteSpec = field(repr=False)
    video: VideoDelivery | None = field(default=None, repr=False)


@dataclass(frozen=True, slots=True)
class MediaChannelSummary:
    device_id: int = field(repr=False)
    media: MediaRouteResult | None
    tcp: TcpMediaResult | None
    failure: str = ""
    protocol_code: str = ""
    protocol_facts: tuple[tuple[str, int], ...] = ()
    udp: UdpMediaResult | None = None


def media_channels_closed(channels: tuple[MediaChannelSummary, ...], *,
                          require_tcp_receipt: bool = True) -> bool:
    """Every admitted channel must retire; primary-channel success is insufficient."""
    return all(item.media is not None
               and (not item.media.opened or item.media.broker_release)
               and (item.tcp is None or (not item.tcp.socket_close_failed
                    and (item.tcp.close_acknowledged or not require_tcp_receipt)))
               and (item.udp is None or item.udp.detached)
               for item in channels)


class AccountMediaChannel:
    """One selected LAN/relay transport; no thread/login/decoder creation."""

    def __init__(self, bootstrap: MediaBootstrap, route: MediaRouteJob, *,
                 clock: Callable[[], float] = time.monotonic,
                 policy: MediaPolicy = MediaPolicy.FINITE):
        self.bootstrap, self.route = bootstrap, route
        self.tcp: CooperativeTcpMedia | None = None
        self.udp: CooperativeUdpMedia | None = None
        self._clock, self._policy = clock, policy
        self._retiring = False
        self._failure = ""
        self._protocol_code = ""
        self._protocol_facts: tuple[tuple[str, int], ...] = ()
        self._stopping = False

    @property
    def ready(self) -> bool:
        return bool((self.tcp is not None and self.tcp.ready)
                    or (self.udp is not None and self.udp.ready))

    @property
    def transport_running(self) -> bool:
        return ((self.tcp is not None and self.tcp.result is None)
                or (self.udp is not None and self.udp.result is None))

    def step(self, opener: Callable[..., CooperativeTcpMedia], *, budget_seconds: float = 0.2,
             udp_send: UdpSend | None = None) -> None:
        if self._retiring:
            return
        if self.udp is not None:
            self.udp.step()
            return
        if self.route.endpoint is not None and self.tcp is None:
            spec, video = self.bootstrap.spec, self.bootstrap.video
            if udp_send is not None and self.route.lan_endpoint is not None:
                if not self.route.lan_probe_complete:
                    return  # Existing owner polls the bounded measurement, no blocking I/O.
                if self.route.nat_endpoint is not None and not self.route.nat_handshake_complete:
                    return
                if self.route.lan_roundtrip_confirmed and (
                        self.route.nat_endpoint is None or self.route.nat_handshake_confirmed):
                    self.udp = CooperativeUdpMedia(self.route, spec, udp_send, clock=self._clock, video=video)
                    self.udp.step()
                    return
            if self._policy is MediaPolicy.CONTINUOUS:
                self.tcp = opener(self.route, spec, clock=self._clock, video=video, policy=self._policy)
            elif video is None:
                self.tcp = opener(self.route, spec, clock=self._clock)
            else:
                self.tcp = opener(self.route, spec, clock=self._clock, video=video)
        if self.tcp is not None:
            if budget_seconds == 0.2:
                self.tcp.step()
            else:
                self.tcp.step(budget_seconds=budget_seconds)

    def request_stop(self) -> None:
        """Cooperative detach; do not spend two seconds draining one sibling here."""
        if self._retiring or self._stopping:
            return
        self._stopping = True
        if self.udp is not None:
            self.udp.begin_finish()
        elif self.tcp is None:
            self.retire("ended")
        else:
            self.tcp.begin_finish()

    def finish_media(self) -> None:
        try:
            if self.tcp is not None and self.tcp.result is None:
                self.tcp.finish()
            if self.udp is not None and self.udp.result is None:
                self.udp.finish()
        finally:
            if self.bootstrap.video is not None:
                self.bootstrap.video.close()

    def retire(self, failure: str, *, protocol_code: str = "",
               protocol_facts: tuple[tuple[str, int], ...] = ()) -> None:
        """No retry. Retire the selected transport before broker CLOSE."""
        if failure not in ("socket", "protocol", "ended"):
            raise ValueError("invalid media channel failure")
        if self._retiring:
            return
        self._retiring = True
        self._failure = failure
        self._protocol_code = protocol_code
        self._protocol_facts = protocol_facts
        try:
            self.finish_media()
        finally:
            self.route.cancel()

    def summary(self) -> MediaChannelSummary:
        return MediaChannelSummary(self.bootstrap.spec.device.device_id, self.route.result,
                                   self.tcp.result if self.tcp is not None else None, self._failure,
                                   self._protocol_code, self._protocol_facts,
                                   self.udp.result if self.udp is not None else None)
