"""Small stopped-session cache. No active-route eviction, heartbeat or movement."""
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

    Key must bind camera, direction, reviewed profile and current credentials.
    Caller retains per-camera ownership throughout acquire/use/return.
    """
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._idle: dict[tuple[object, ...], _Idle] = {}

    def acquire(self, key: tuple[object, ...], prepare: Callable[[], NativePtzRoute]) -> CachedPtzRoute:
        with self._lock:
            idle = self._idle.pop(key, None)
        now = time.monotonic()
        if idle is not None:
            idle.timer.cancel()
            if now - idle.created < 20 and now < idle.expires:
                try:
                    return CachedPtzRoute(self, key, idle.route.renew(), idle.created, True)
                except BaseException as exc:
                    idle.route.close()
                    if isinstance(exc, Exception):
                        raise P2PProbeError("cached PTZ session could not be renewed") from exc
                    raise
            idle.route.close()
        return CachedPtzRoute(self, key, prepare(), now, False)

    def _return(self, lease: CachedPtzRoute) -> None:
        remaining = 20 - (time.monotonic() - lease.created)
        if remaining <= 0 or not lease.confirmed or lease.failed:
            lease.route.close()
            return
        timer = threading.Timer(min(8, remaining), self._expire, args=(lease.key, lease.route))
        timer.daemon = True
        with self._lock:
            if len(self._idle) >= 4 or lease.key in self._idle:
                lease.route.close()
                return
            self._idle[lease.key] = _Idle(lease.route, lease.created, timer, time.monotonic()+min(8, remaining))
            try:
                timer.start()
            except BaseException:
                self._idle.pop(lease.key)
                lease.route.close()
                raise

    def _expire(self, key: tuple[object, ...], route: NativePtzRoute) -> None:
        with self._lock:
            current = self._idle.get(key)
            if current is None or current.route is not route:
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
