"""Small pristine/stopped-session cache; broker liveness checks, never movement."""
from __future__ import annotations

import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass

from . import broker_epoch
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
    checked: float
    epoch: int


class PtzRouteCache:
    """At most four idle sockets, with an 8s idle timeout.

    Shared accounts survive while maintained; private diagnostic routes retain
    their 20s absolute bound. Target selection independently refreshes evidence.

    Key must bind camera, reviewed profile and current credentials. Direction changes
    are permitted only within the axes verified when the route was prepared.
    Caller retains per-camera ownership throughout acquire/use/return.
    """
    def __init__(self, *, shared_account: bool = False) -> None:
        self._lock = threading.Lock()
        self._idle: dict[tuple[object, ...], _Idle] = {}
        self._shared_account = shared_account
        self._retired = threading.Condition(self._lock)
        self._closing = 0
        self._close_failed = False
        self._paused = False

    def _check_admission(self) -> None:
        # Caller holds _lock. Does not replace the external broker reservation.
        if self._paused or self._close_failed:
            raise P2PProbeError("PTZ cache paused or cleanup unconfirmed")

    def _close_tracked(self, route: NativePtzRoute) -> None:
        """Caller registers _closing before removing the route from the cache."""
        failed = True
        try:
            route.close()
            failed = False
        finally:
            with self._retired:
                self._close_failed |= failed
                self._closing -= 1
                self._retired.notify_all()

    @contextmanager
    def quiesce(self) -> Iterator[None]:
        """Experimental handoff: caller holds broker ownership through the yield.

        No active leases may exist; the native owner checks motion/warmup first.
        Pauses cache admission, retires idle routes and waits <=1s for any timer
        already closing a removed route. Uncertain close permanently blocks reuse.
        Does not authenticate or close a borrowed/active socket.
        """
        with self._lock:
            self._check_admission()
            self._paused = True
        try:
            self.close_idle()
            with self._retired:
                if not self._retired.wait_for(lambda: self._closing == 0, timeout=1.0):
                    self._close_failed = True
                if self._close_failed:
                    raise P2PProbeError("PTZ cache cleanup unconfirmed")
            yield
        finally:
            with self._lock:
                self._paused = False

    def _storage_key(self, key: tuple[object, ...]) -> tuple[object, ...]:
        if not self._shared_account:
            return key
        if len(key) != 6:
            raise ValueError("complete PTZ credential/profile key required")
        return ("account", key[3], key[4])

    def warm(self, key: tuple[object, ...], prepare: Callable[[], NativePtzRoute], *,
             select: Callable[[NativePtzRoute], NativePtzRoute] | None = None) -> bool:
        """Keep a healthy stopped account, without periodic reauthentication."""
        if self._shared_account and select is None:
            raise ValueError("shared PTZ session requires explicit target selection")
        now = time.monotonic()
        with self._lock:
            self._check_admission()
            idle = self._idle.pop(self._storage_key(key), None)
            full = len(self._idle) >= 4
        if idle is not None:
            idle.timer.cancel()
            if self._shared_account and idle.epoch != broker_epoch.current():
                idle.route.close()
                idle = None
        if idle is not None:
            if now < idle.expires and (self._shared_account or now - idle.created < 15):
                route = idle.route
                try:
                    if not self._shared_account or now - idle.checked >= 2:
                        route.keepalive()
                        idle.checked = time.monotonic()
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
                lease = CachedPtzRoute(self, key, route, idle.created, True, checked=idle.checked)
                return self._return(lease, prepared=True)
            idle.route.close()
        if full:
            return False
        lease = CachedPtzRoute(self, key, prepare(), now, False)
        return self._return(lease, prepared=True)

    def close_idle(self) -> None:
        with self._lock:
            idle, self._idle = self._idle, {}
            self._closing += len(idle)
        failure: BaseException | None = None
        for item in idle.values():
            try:
                try:
                    item.timer.cancel()
                finally:
                    self._close_tracked(item.route)
            except BaseException as exc:
                if failure is None:
                    failure = exc
        if failure is not None:
            raise failure

    def acquire(self, key: tuple[object, ...], prepare: Callable[[], NativePtzRoute],
                *, direction: str | None = None,
                select: Callable[[NativePtzRoute], NativePtzRoute] | None = None) -> CachedPtzRoute:
        if self._shared_account and select is None:
            raise ValueError("shared PTZ session requires explicit target selection")
        with self._lock:
            self._check_admission()
            idle = self._idle.pop(self._storage_key(key), None)
        now = time.monotonic()
        if idle is not None:
            idle.timer.cancel()
            if self._shared_account and idle.epoch != broker_epoch.current():
                idle.route.close()
                idle = None
        if idle is not None:
            if (self._shared_account or now - idle.created < 20) and now < idle.expires:
                if self._shared_account:
                    try:
                        if now - idle.checked >= 2:
                            idle.route.keepalive()
                            idle.checked = time.monotonic()
                    except (OSError, P2PProbeError):
                        idle.route.close()
                        created = time.monotonic()
                        return CachedPtzRoute(self, key, prepare(), created, False)
                try:
                    renewed = (select(idle.route) if select is not None else
                               idle.route.renew() if direction is None else idle.route.renew(direction))
                    return CachedPtzRoute(self, key, renewed, idle.created, True, checked=idle.checked)
                except BaseException as exc:
                    idle.route.close()
                    if isinstance(exc, Exception):
                        raise P2PProbeError("cached PTZ session could not be renewed") from exc
                    raise
            idle.route.close()
        return CachedPtzRoute(self, key, prepare(), now, False)

    def _return(self, lease: CachedPtzRoute, *, prepared: bool = False) -> bool:
        remaining = 8 if self._shared_account else 20 - (time.monotonic() - lease.created)
        if (remaining <= 0 or (not prepared and not lease.confirmed) or lease.failed
                or (self._shared_account and lease.epoch != broker_epoch.current())):
            lease.route.close()
            return False
        key = self._storage_key(lease.key)
        timer = threading.Timer(min(8, remaining), self._expire, args=(key, lease.route))
        timer.daemon = True
        with self._lock:
            if self._paused or self._close_failed or len(self._idle) >= 4 or key in self._idle:
                lease.route.close()
                return False
            self._idle[key] = _Idle(lease.route, lease.created, timer,
                                  time.monotonic()+min(8, remaining), lease.checked, lease.epoch)
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
            self._closing += 1
        self._close_tracked(route)


class CachedPtzRoute:
    def __init__(self, pool: PtzRouteCache, key: tuple[object, ...], route: NativePtzRoute,
                 created: float, reused: bool, *, checked: float | None = None) -> None:
        self.pool, self.key, self.route = pool, key, route
        self.created, self.reused = created, reused
        self.checked = time.monotonic() if checked is None else checked
        self.epoch = broker_epoch.current()
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
