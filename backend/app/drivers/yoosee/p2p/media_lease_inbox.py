"""Bounded cross-thread source leases; only the account reader drives transport."""

import threading
from dataclasses import dataclass, field

from ....camera_identity import valid_camera_id
from .account_media_channel import MediaBootstrap, MediaChannelSummary, media_channels_closed
from .account_policy import validate_media_capacity
from .contracts import OnlineDevice
from .media_route_job import MediaRouteSpec
from .video_delivery import VideoDelivery


@dataclass(frozen=True, slots=True)
class MediaLeaseTicket:
    generation: int
    serial: int


@dataclass(frozen=True, slots=True)
class MediaLeaseOutcome:
    status: str
    channel: MediaChannelSummary | None = None


@dataclass(slots=True)
class _Lease:
    camera_id: str
    bootstrap: MediaBootstrap = field(repr=False)
    claimed: bool = False
    cancelled: bool = False
    result: MediaLeaseOutcome | None = None


class MediaLeaseInbox:
    """No sockets, login, retry or threads. Admission is trusted-driver only.

    Slots include unread terminal results. A source cannot free a claimed slot
    by cancelling it; its channel must finish cleanup first. Stale tickets never
    address a replacement. Account identity is fixed before admitting any lease.
    """

    def __init__(self, generation: int, access_id: int, *,
                 targets: tuple[OnlineDevice, ...], capacity: int = 3):
        validate_media_capacity(capacity)
        if (type(generation) is not int or not 0 < generation < 1 << 64
                or type(access_id) is not int or not 0 <= access_id < 1 << 64):
            raise ValueError("typed account lease identity required")
        if (not isinstance(targets, tuple) or not 1 <= len(targets) <= capacity
                or any(not isinstance(target, OnlineDevice) or type(target.device_id) is not int
                       or not 0 < target.device_id < 1 << 64 for target in targets)
                or len({target.device_id for target in targets}) != len(targets)):
            raise ValueError("bounded authenticated lease targets required")
        self._generation, self._capacity = generation, capacity
        self._access_id = access_id
        self._targets = targets
        self._lock = threading.Lock()
        self._closed = False
        self._serial = 0
        self._leases: dict[MediaLeaseTicket, _Lease] = {}

    @property
    def generation(self) -> int:
        return self._generation

    @property
    def capacity(self) -> int:
        return self._capacity

    def matches_account(self, access_id: int) -> bool:
        return type(access_id) is int and access_id == self._access_id

    def matches_targets(self, targets: tuple[OnlineDevice, ...]) -> bool:
        return self._targets == targets

    def permits_target(self, target: OnlineDevice) -> bool:
        return target in self._targets and bool(target.status)

    def submit(self, camera_id: str, bootstrap: MediaBootstrap) -> MediaLeaseTicket:
        if (type(camera_id) is not str or not valid_camera_id(camera_id) or not isinstance(bootstrap, MediaBootstrap)
                or not isinstance(bootstrap.spec, MediaRouteSpec)
                or not isinstance(bootstrap.spec.device, OnlineDevice)
                or type(bootstrap.spec.access_id) is not int
                or bootstrap.spec.access_id != self._access_id
                or type(bootstrap.spec.device.device_id) is not int
                or not 0 < bootstrap.spec.device.device_id < 1 << 64
                or not bootstrap.spec.device.status
                or bootstrap.spec.device not in self._targets
                or not isinstance(bootstrap.video, VideoDelivery) or bootstrap.video.closed):
            raise ValueError("fresh exact-account source lease required")
        with self._lock:
            if self._closed or len(self._leases) >= self.capacity or self._serial >= 1 << 64:
                raise ValueError("media lease capacity unavailable")
            if any(lease.camera_id == camera_id
                   or lease.bootstrap.spec.device.device_id == bootstrap.spec.device.device_id
                   or lease.bootstrap.video is bootstrap.video for lease in self._leases.values()):
                raise ValueError("duplicate media lease identity or delivery")
            ticket = MediaLeaseTicket(self.generation, self._serial)
            self._serial += 1
            self._leases[ticket] = _Lease(camera_id, bootstrap)
            return ticket

    def claim(self) -> tuple[MediaLeaseTicket, MediaBootstrap] | None:
        with self._lock:
            if self._closed:
                return None
            for ticket, lease in self._leases.items():
                if not lease.claimed and lease.result is None:
                    if lease.bootstrap.video is None or lease.bootstrap.video.closed:
                        lease.cancelled = True
                        lease.result = MediaLeaseOutcome("cancelled")
                        continue
                    lease.claimed = True
                    return ticket, lease.bootstrap
            return None

    def cancel(self, ticket: MediaLeaseTicket) -> bool:
        video = None
        with self._lock:
            lease = self._leases.get(ticket)
            if lease is None or lease.result is not None:
                return False
            lease.cancelled = True
            if not lease.claimed:
                lease.result = MediaLeaseOutcome("cancelled")
                video = lease.bootstrap.video
        if video is not None:
            video.close()
        return True

    def should_cancel(self, ticket: MediaLeaseTicket) -> bool:
        with self._lock:
            lease = self._leases.get(ticket)
            return (self._closed or lease is None or lease.cancelled
                    or lease.bootstrap.video is None or lease.bootstrap.video.closed)

    def finish(self, ticket: MediaLeaseTicket, channel: MediaChannelSummary, *, uncertain: bool = False) -> None:
        with self._lock:
            lease = self._leases.get(ticket)
            if lease is None or not lease.claimed or lease.result is not None:
                return
            if channel.device_id != lease.bootstrap.spec.device.device_id:
                raise ValueError("media lease completion identity mismatch")
            clean = not uncertain and media_channels_closed((channel,), require_tcp_receipt=False)
            lease.result = MediaLeaseOutcome("closed" if clean else "blocked", channel)
            if not clean:
                self._closed = True

    def take(self, ticket: MediaLeaseTicket) -> MediaLeaseOutcome | None:
        with self._lock:
            lease = self._leases.get(ticket)
            if lease is None or lease.result is None:
                return None
            result = lease.result
            del self._leases[ticket]
            return result

    def close(self) -> None:
        with self._lock:
            self._closed = True
            tickets = tuple(self._leases)
        for ticket in tickets:
            self.cancel(ticket)
