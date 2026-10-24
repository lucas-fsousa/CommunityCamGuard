"""Driver-neutral, caller-owned source admission. No background jobs or camera I/O.

Driver factories must be lazy; source.run() returning normally certifies completed
cleanup, including its consumers. The caller owns the run thread and quotas. This
registry does not start RTSP fallback or infer capabilities from a camera brand.
"""

import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Protocol

from ..camera_identity import valid_camera_id
from .mp4_fanout import Subscription


class SourceUnavailable(RuntimeError):
    pass


class SourceCleanupUnconfirmed(SourceUnavailable):
    pass


class ManagedSource(Protocol):
    def run(self, *, cancelled: Callable[[], bool]) -> object: ...
    def subscribe(self) -> Subscription: ...


@dataclass(eq=False)
class _Entry:
    domain: str = field(repr=False)
    owner: object = field(default_factory=object, repr=False)
    stop: threading.Event = field(default_factory=threading.Event, repr=False)
    source: ManagedSource | None = field(default=None, repr=False)
    phase: str = "starting"


class SourceRegistry:
    """At most one source per public camera ID AND per driver resource domain.

    Domain keys come from trusted drivers, never from a browser or access token.
    Blocked entries consume capacity until the registry/process is replaced; no
    optimistic reset, unbounded failure history or authentication retry exists.
    """

    def __init__(self, *, capacity: int = 1):
        if type(capacity) is not int or not 1 <= capacity <= 32:
            raise ValueError("source capacity must be between 1 and 32")
        self._capacity = capacity
        self._lock = threading.Lock()
        self._entries: dict[str, _Entry] = {}
        self._closed = False

    @staticmethod
    def _validate(camera_id: str) -> None:
        if type(camera_id) is not str or not valid_camera_id(camera_id):
            raise ValueError("public camera ID required")

    def state(self, camera_id: str) -> str:
        self._validate(camera_id)
        with self._lock:
            entry = self._entries.get(camera_id)
            return entry.phase if entry is not None else "idle"

    def camera_ids(self) -> tuple[str, ...]:
        """Snapshot all reservations, including stopping/uncertain cleanup.

        Delivery must not reopen an old ingress merely because a native source
        is temporarily unready or its cleanup has not been confirmed.
        """
        with self._lock:
            return tuple(self._entries)

    def owns(self, camera_id: str, owner: object) -> bool:
        """Include blocked reservations; absence alone is not a cleanup receipt."""
        self._validate(camera_id)
        with self._lock:
            entry = self._entries.get(camera_id)
            return entry is not None and entry.owner is owner

    def states(self) -> dict[str, str]:
        """Metadata only: no subscriptions, worker handles, domains or credentials."""
        with self._lock:
            return {camera_id: "stopping" if self._closed and entry.phase != "blocked" else entry.phase
                    for camera_id, entry in self._entries.items()}

    def run(self, camera_id: str, *, domain: str,
            factory: Callable[[], ManagedSource], cancelled: Callable[[], bool],
            owner: object | None = None) -> object:
        self._validate(camera_id)
        if (type(domain) is not str or not 1 <= len(domain) <= 128
                or not domain.isascii() or any(ord(c) < 33 or ord(c) > 126 for c in domain)):
            raise ValueError("driver resource domain required")
        if cancelled():
            raise SourceUnavailable("source admission cancelled")
        with self._lock:
            if self._closed:
                raise SourceUnavailable("source registry shutting down")
            if (camera_id in self._entries or len(self._entries) >= self._capacity
                    or any(entry.domain == domain for entry in self._entries.values())):
                raise SourceUnavailable("source or driver resource already reserved")
            entry = _Entry(domain, owner=owner if owner is not None else object())
            self._entries[camera_id] = entry
        confirmed = False
        try:
            source = factory()
            with self._lock:
                entry.source = source
                entry.phase = "stopping" if entry.stop.is_set() else "running"
            result = source.run(cancelled=lambda: entry.stop.is_set() or cancelled())
            with self._lock:
                if entry.phase == "blocked":
                    raise SourceCleanupUnconfirmed("source cleanup uncertain")
                confirmed = True
            return result
        finally:
            with self._lock:
                if confirmed and entry.phase != "blocked":
                    del self._entries[camera_id]
                else:
                    entry.phase = "blocked"

    def subscribe(self, camera_id: str, *, owner: object | None = None) -> Subscription:
        self._validate(camera_id)
        with self._lock:
            entry = self._entries.get(camera_id)
            if (self._closed or entry is None or entry.phase != "running" or entry.source is None
                    or (owner is not None and entry.owner is not owner)):
                raise SourceUnavailable("source is not accepting readers")
            source = entry.source
        # Source owns readiness and invalidates subscriptions during teardown.
        return source.subscribe()

    def stop(self, camera_id: str, *, owner: object | None = None) -> bool:
        """Signal only; reservation persists until run() has confirmed cleanup."""
        self._validate(camera_id)
        with self._lock:
            entry = self._entries.get(camera_id)
            if entry is None or (owner is not None and entry.owner is not owner):
                return False
            if entry.phase == "blocked":
                raise SourceCleanupUnconfirmed("source cleanup already uncertain")
            entry.stop.set()
            entry.phase = "stopping"
        return True

    def begin_shutdown(self) -> None:
        """Reject admission and signal all owners. Caller must still join run threads."""
        with self._lock:
            self._closed = True
            cameras = tuple(self._entries)
        failed = False
        for camera_id in cameras:
            try:
                self.stop(camera_id)
            except SourceCleanupUnconfirmed:
                failed = True
        if failed:
            raise SourceCleanupUnconfirmed("one or more sources have uncertain cleanup")
