"""Small pristine/stopped-session cache; broker liveness checks, never movement."""
from __future__ import annotations

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass

from .contracts import P2PProbeError
from .ptz_route import NativePtzRoute


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
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._idle: dict[tuple[object, ...], _Idle] = {}

    def warm(self, key: tuple[object, ...], prepare: Callable[[], NativePtzRoute]) -> bool:
        """Retain a never-started/stopped route; replace before its absolute deadline.

        Caller owns this camera exclusively. Broker heartbeats validate liveness
        without extending the 20s absolute lifetime. At most four idle sockets.
        """
        now = time.monotonic()
        with self._lock:
            idle = self._idle.pop(key, None)
            full = len(self._idle) >= 4
        if idle is not None:
            idle.timer.cancel()
            if now < idle.expires and now - idle.created < 15:
                try:
                    idle.route.keepalive()
                except BaseException:
                    idle.route.close()
                    raise
                lease = CachedPtzRoute(self, key, idle.route, idle.created, True)
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
                *, direction: str | None = None) -> CachedPtzRoute:
        with self._lock:
            idle = self._idle.pop(key, None)
        now = time.monotonic()
        if idle is not None:
            idle.timer.cancel()
            if now - idle.created < 20 and now < idle.expires:
                try:
                    renewed = idle.route.renew() if direction is None else idle.route.renew(direction)
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
        timer = threading.Timer(min(8, remaining), self._expire, args=(lease.key, lease.route))
        timer.daemon = True
        with self._lock:
            if len(self._idle) >= 4 or lease.key in self._idle:
                lease.route.close()
                return False
            self._idle[lease.key] = _Idle(lease.route, lease.created, timer, time.monotonic()+min(8, remaining))
            try:
                timer.start()
            except BaseException:
                self._idle.pop(lease.key)
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
