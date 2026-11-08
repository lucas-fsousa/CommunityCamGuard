"""Sole-reader lease admission/retirement; no sockets, threads or authentication."""

import time
from collections.abc import Callable

from .account_media_channel import (
    AccountMediaChannel,
    MediaChannelSummary,
    media_channels_closed,
    media_channels_locally_retired,
)
from .media_lease_inbox import MediaLeaseInbox, MediaLeaseTicket
from .media_policy import MediaPolicy
from .media_route_job import MediaRouteJob, MediaRouteResult, MediaRouteSpec


class MediaAccountRetirement(Exception):
    """Stop the account rather than publish a missing remote ACK as success."""


class MediaLeasePump:
    def __init__(self, inbox: MediaLeaseInbox, channels: list[AccountMediaChannel], *,
                 clock: Callable[[], float] = time.monotonic):
        self.inbox, self._channels, self._clock = inbox, channels, clock
        self._active: dict[MediaLeaseTicket, AccountMediaChannel] = {}
        self.cleanup_uncertain = False
        self.account_retired = False

    def _complete(self, ticket: MediaLeaseTicket, channel: AccountMediaChannel, *, uncertain: bool = False,
                  account_retired: bool = False) -> bool:
        summary = channel.summary()
        clean = media_channels_closed((summary,), require_tcp_receipt=False) and not uncertain
        retired = not uncertain and account_retired and media_channels_locally_retired((summary,))
        self.cleanup_uncertain |= not (clean or retired)
        self.account_retired |= retired and not clean
        self.inbox.finish(ticket, summary, uncertain=not (clean or retired), account_retired=retired)
        return clean or retired

    def motion_ready(self, device_id: int) -> bool:
        return any(channel.bootstrap.spec.device.device_id == device_id
                   and not self.inbox.should_cancel(ticket)
                   and channel.ready
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
                if channel.transport_running:
                    raise ValueError("media route ended before transport retirement")
                summary = channel.summary()
                if (not media_channels_closed((summary,), require_tcp_receipt=False)
                        and media_channels_locally_retired((summary,))):
                    self.inbox.close()
                    raise MediaAccountRetirement
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

    def finish(self, *, uncertain: bool, account_socket_closed: bool = False) -> None:
        self.inbox.close()
        retired = (account_socket_closed and not uncertain
                   and media_channels_locally_retired(tuple(c.summary() for c in self._active.values())))
        for ticket, channel in self._active.items():
            if channel.bootstrap.video is not None:
                channel.bootstrap.video.close()
            self._complete(ticket, channel, uncertain=uncertain, account_retired=retired)
