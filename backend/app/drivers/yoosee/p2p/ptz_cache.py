"""Small pristine/stopped-session cache; broker liveness checks, never movement."""
from __future__ import annotations

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass

from .contracts import P2PProbeError
from .ptz_route import NativePtzRoute


class PtzKeepaliveError(P2PProbeError):
    """A cached broker route failed its read-only liveness check."""


@dataclass
class _Idle:
    route: NativePtzRoute
    created: float
    timer: threading.Timer
    expires: float


class PtzRouteCache:
    """At most four idle sockets, 8s idle/20s absolute lifetime including preparation.

    Key must bind camera, reviewed profile and current credentials. Direction changes
    are permitted only within the axes verified when the route was prepared.
    Caller retains per-camera ownership throughout acquire/use/return.
    """
    def __init__(self, *, shared_account: bool = False) -> None:
        self._lock = threading.Lock()
        self._idle: dict[tuple[object, ...], _Idle] = {}
        self._shared_account = shared_account

    def _storage_key(self, key: tuple[object, ...]) -> tuple[object, ...]:
        if not self._shared_account:
            return key
        if len(key) != 6:
            raise ValueError("complete PTZ credential/profile key required")
        return ("account", key[3], key[4])

    def warm(self, key: tuple[object, ...], prepare: Callable[[], NativePtzRoute], *,
             select: Callable[[NativePtzRoute], NativePtzRoute] | None = None) -> bool:
        """Retain a never-started/stopped route; replace before its absolute deadline.

        Caller owns this camera exclusively. Broker heartbeats validate liveness
        without extending the 20s absolute lifetime. At most four idle sockets.
        """
        if self._shared_account and select is None:
            raise ValueError("shared PTZ session requires explicit target selection")
        now = time.monotonic()
        with self._lock:
            idle = self._idle.pop(self._storage_key(key), None)
            full = len(self._idle) >= 4
        if idle is not None:
            idle.timer.cancel()
            if now < idle.expires and now - idle.created < 15:
                route = idle.route
                try:
                    route.keepalive()
                except BaseException as exc:
                    route.close()
                    if isinstance(exc, Exception):
                        raise PtzKeepaliveError("PTZ broker liveness check failed") from exc
                    raise
                try:
                    if select is not None:
                        route = select(route)
                except BaseException:
                    route.close()
                    raise
                lease = CachedPtzRoute(self, key, route, idle.created, True)
                return self._return(lease, prepared=True)
            idle.route.close()
        if full:
            return False
        lease = CachedPtzRoute(self, key, prepare(), now, False)
        return self._return(lease, prepared=True)

    def close_idle(self) -> None:
        with self._lock:
            idle, self._idle = self._idle, {}
        for item in idle.values():
            item.timer.cancel()
            item.route.close()

    def acquire(self, key: tuple[object, ...], prepare: Callable[[], NativePtzRoute],
                *, direction: str | None = None,
                select: Callable[[NativePtzRoute], NativePtzRoute] | None = None) -> CachedPtzRoute:
        if self._shared_account and select is None:
            raise ValueError("shared PTZ session requires explicit target selection")
        with self._lock:
            idle = self._idle.pop(self._storage_key(key), None)
        now = time.monotonic()
        if idle is not None:
            idle.timer.cancel()
            if now - idle.created < 20 and now < idle.expires:
                if self._shared_account:
                    try:
                        idle.route.keepalive()
                    except (OSError, P2PProbeError):
                        idle.route.close()
                        created = time.monotonic()
                        return CachedPtzRoute(self, key, prepare(), created, False)
                try:
                    renewed = (select(idle.route) if select is not None else
                               idle.route.renew() if direction is None else idle.route.renew(direction))
                    return CachedPtzRoute(self, key, renewed, idle.created, True)
                except BaseException as exc:
                    idle.route.close()
                    if isinstance(exc, Exception):
                        raise P2PProbeError("cached PTZ session could not be renewed") from exc
                    raise
            idle.route.close()
        return CachedPtzRoute(self, key, prepare(), now, False)

    def _return(self, lease: CachedPtzRoute, *, prepared: bool = False) -> bool:
        remaining = 20 - (time.monotonic() - lease.created)
        if remaining <= 0 or (not prepared and not lease.confirmed) or lease.failed:
            lease.route.close()
            return False
        key = self._storage_key(lease.key)
        timer = threading.Timer(min(8, remaining), self._expire, args=(key, lease.route))
        timer.daemon = True
        with self._lock:
            if len(self._idle) >= 4 or key in self._idle:
                lease.route.close()
                return False
            self._idle[key] = _Idle(lease.route, lease.created, timer, time.monotonic()+min(8, remaining))
            try:
                timer.start()
            except BaseException:
                self._idle.pop(key)
                lease.route.close()
                raise
        return True

    def _expire(self, key: tuple[object, ...], route: NativePtzRoute) -> None:
        with self._lock:
            current = self._idle.get(key)
            if current is None or current.route is not route or time.monotonic() < current.expires:
                return
            self._idle.pop(key)
        route.close()


class CachedPtzRoute:
    def __init__(self, pool: PtzRouteCache, key: tuple[object, ...], route: NativePtzRoute,
                 created: float, reused: bool) -> None:
        self.pool, self.key, self.route = pool, key, route
        self.created, self.reused = created, reused
        self.failed = self.confirmed = self.closed = False

    def send_start(self) -> None:
        if self.closed:
            raise RuntimeError("PTZ lease is closed")
        try:
            self.route.send_start()
        except BaseException:
            self.failed = True
            raise

    def send_release(self) -> None:
        if self.closed:
            raise RuntimeError("PTZ lease is closed")
        try:
            self.route.send_release()
        except BaseException:
            self.failed = True
            raise

    def confirm_release(self, *, deadline: float) -> bool:
        if self.closed:
            raise RuntimeError("PTZ lease is closed")
        try:
            self.confirmed = self.route.confirm_release(deadline=deadline)
            return self.confirmed
        except BaseException:
            self.failed = True
            raise

    def close(self) -> None:
        if not self.closed:
            self.closed = True
            self.pool._return(self)
