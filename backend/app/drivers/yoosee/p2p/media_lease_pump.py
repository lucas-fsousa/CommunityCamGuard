"""Sole-reader lease admission/retirement; no sockets, threads or authentication."""

import time
from collections.abc import Callable

from .account_media_channel import AccountMediaChannel, MediaChannelSummary, media_channels_closed
from .media_lease_inbox import MediaLeaseInbox, MediaLeaseTicket
from .media_policy import MediaPolicy
from .media_route_job import MediaRouteJob, MediaRouteResult, MediaRouteSpec


class MediaLeasePump:
    def __init__(self, inbox: MediaLeaseInbox, channels: list[AccountMediaChannel], *,
                 clock: Callable[[], float] = time.monotonic):
        self.inbox, self._channels, self._clock = inbox, channels, clock
        self._active: dict[MediaLeaseTicket, AccountMediaChannel] = {}
        self.cleanup_uncertain = False

    def _complete(self, ticket: MediaLeaseTicket, channel: AccountMediaChannel, *, uncertain: bool = False) -> bool:
        summary = channel.summary()
        clean = media_channels_closed((summary,), require_tcp_receipt=False) and not uncertain
        self.cleanup_uncertain |= not clean
        self.inbox.finish(ticket, summary, uncertain=not clean)
        return clean

    def motion_ready(self, device_id: int) -> bool:
        return any(channel.bootstrap.spec.device.device_id == device_id
                   and not self.inbox.should_cancel(ticket)
                   and channel.tcp is not None and channel.tcp.ready
                   for ticket, channel in self._active.items())

    def any_motion_ready(self) -> bool:
        return any(self.motion_ready(channel.bootstrap.spec.device.device_id)
                   for channel in self._active.values())

    def poll(self, prepare: Callable[[MediaRouteSpec], MediaRouteJob], *,
             defer_stop_device: int | None = None) -> None:
        for ticket, channel in tuple(self._active.items()):
            if channel.bootstrap.spec.device.device_id == defer_stop_device:
                continue  # Motor STOP/receipt precedes detach/result publication.
            if channel.route.result is not None:
                if channel.tcp is not None and channel.tcp.result is None:
                    raise ValueError("media route ended before TCP retirement")
                if not self._complete(ticket, channel):
                    raise ValueError("media lease cleanup unconfirmed")
                self._channels.remove(channel)
                del self._active[ticket]
            elif self.inbox.should_cancel(ticket):
                channel.request_stop()
        claimed = self.inbox.claim()
        if claimed is None:
            return
        ticket, bootstrap = claimed
        try:
            route = prepare(bootstrap.spec)
        except BaseException:
            if bootstrap.video is not None:
                bootstrap.video.close()
            self.inbox.finish(ticket, MediaChannelSummary(bootstrap.spec.device.device_id,
                MediaRouteResult(False, False, False), None, "protocol"))
            raise
        channel = AccountMediaChannel(bootstrap, route, clock=self._clock, policy=MediaPolicy.CONTINUOUS)
        self._active[ticket] = channel
        self._channels.append(channel)

    def finish(self, *, uncertain: bool) -> None:
        self.inbox.close()
        for ticket, channel in self._active.items():
            if channel.bootstrap.video is not None:
                channel.bootstrap.video.close()
            self._complete(ticket, channel, uncertain=uncertain)
