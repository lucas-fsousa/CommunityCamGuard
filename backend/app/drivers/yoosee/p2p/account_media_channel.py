"""One media transport under the account worker; never reads its broker socket."""

import time
from collections.abc import Callable
from dataclasses import dataclass, field

from .cooperative_tcp_media import CooperativeTcpMedia, TcpMediaResult
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


def media_channels_closed(channels: tuple[MediaChannelSummary, ...], *,
                          require_tcp_receipt: bool = True) -> bool:
    """Every admitted channel must retire; primary-channel success is insufficient."""
    return all(item.media is not None
               and (not item.media.opened or item.media.broker_release)
               and (item.tcp is None or (not item.tcp.socket_close_failed
                    and (item.tcp.close_acknowledged or not require_tcp_receipt)))
               for item in channels)


class AccountMediaChannel:
    """Independent route, TCP state and delivery; no thread/login/decoder creation."""

    def __init__(self, bootstrap: MediaBootstrap, route: MediaRouteJob, *,
                 clock: Callable[[], float] = time.monotonic,
                 policy: MediaPolicy = MediaPolicy.FINITE):
        self.bootstrap, self.route = bootstrap, route
        self.tcp: CooperativeTcpMedia | None = None
        self._clock, self._policy = clock, policy
        self._retiring = False
        self._failure = ""

    def step(self, opener: Callable[..., CooperativeTcpMedia], *, budget_seconds: float = 0.2) -> None:
        if self._retiring:
            return
        if self.route.endpoint is not None and self.tcp is None:
            spec, video = self.bootstrap.spec, self.bootstrap.video
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

    def finish_tcp(self) -> None:
        try:
            if self.tcp is not None and self.tcp.result is None:
                self.tcp.finish()
        finally:
            if self.bootstrap.video is not None:
                self.bootstrap.video.close()

    def retire(self, failure: str) -> None:
        """No retry. Physical TCP cleanup precedes asynchronous broker CLOSE."""
        if failure not in ("socket", "protocol", "ended"):
            raise ValueError("invalid media channel failure")
        if self._retiring:
            return
        self._retiring = True
        self._failure = failure
        try:
            self.finish_tcp()
        finally:
            self.route.cancel()

    def summary(self) -> MediaChannelSummary:
        return MediaChannelSummary(self.bootstrap.spec.device.device_id, self.route.result,
                                   self.tcp.result if self.tcp is not None else None, self._failure)
