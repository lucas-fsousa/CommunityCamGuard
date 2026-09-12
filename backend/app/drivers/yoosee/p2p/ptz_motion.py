"""Bounded native PTZ ownership, independent of HTTP and socket establishment.

Not wired into the dashboard. A reviewed adapter must supply a prepared, exact-device
route with nonblocking sends and deadline-bounded receipt collection. Receipt success
means delivery only; it never proves a motor has physically stopped.
"""
from __future__ import annotations

import math
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Protocol


class PtzBusy(RuntimeError):
    """Do not queue another gesture or switch transports while this camera is owned."""


class PreparedPtzRoute(Protocol):
    """Owns one pinned direction/device/session; never reconnects or falls back.

    send_start/send_release must be nonblocking. confirm_release may observe only
    the RELEASE request, must honor its deadline and give explicit errors precedence.
    """

    def send_start(self) -> None: ...
    def send_release(self) -> None: ...
    def confirm_release(self, *, deadline: float) -> bool: ...
    def close(self) -> None: ...


@dataclass(frozen=True, slots=True)
class MotionResult:
    start_attempted: bool
    cancelled: bool
    release_attempts: int
    release_delivery_confirmed: bool
    error_types: tuple[str, ...]

    @property
    def fallback_allowed(self) -> bool:
        # Cancellation is user intent, not an excuse to move through another protocol.
        return not self.start_attempted and not self.cancelled


class PtzMotion:
    """One non-renewable <=500 ms gesture; RELEASE never waits for START's ACK.

    The owner may run this in its existing bounded worker. stop() is thread-safe and
    shortens the lease. There is no timer/thread per repeat event, no lease extension,
    no implicit motion retry, and no assumption that the browser remains connected.
    """

    def __init__(self, duration: float, *, clock: Callable[[], float] = time.monotonic) -> None:
        if type(duration) not in (int, float) or not math.isfinite(duration) or not 0.10 <= duration <= 0.50:
            raise ValueError("native PTZ gesture must be 0.10..0.50 seconds")
        self._duration = duration
        self._clock = clock
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._used = False

    def stop(self) -> None:
        self._stop.set()

    def run(self, route: PreparedPtzRoute) -> MotionResult:
        with self._lock:
            if self._used:
                raise PtzBusy("gesture has already been claimed")
            self._used = True
        started = False
        attempts = 0
        confirmed = False
        errors: list[str] = []
        cancelled = self._stop.is_set()
        try:
            if not cancelled:
                expires = self._clock() + self._duration
                # Mark BEFORE send: an exception cannot prove that no datagram was delivered.
                started = True
                try:
                    route.send_start()
                    self._stop.wait(max(0.0, expires - self._clock()))
                except Exception as exc:
                    errors.append(type(exc).__name__)
                finally:
                    # Always attempt RELEASE, including ambiguous START send failure. Only
                    # RELEASE may be repeated, on this same prepared route/direction.
                    deadline = self._clock() + 2.0
                    for _ in range(3):
                        attempts += 1
                        try:
                            route.send_release()
                            confirmed = route.confirm_release(deadline=min(deadline, self._clock() + 0.5)) is True
                        except Exception as exc:
                            errors.append(type(exc).__name__)
                        if confirmed or self._clock() >= deadline:
                            break
        finally:
            try:
                route.close()
            except Exception as exc:
                errors.append(type(exc).__name__)
        return MotionResult(started, cancelled or self._stop.is_set(), attempts, confirmed, tuple(errors))


class PtzOwners:
    """Bounded no-queue reservations, acquired BEFORE preparing any camera route."""

    def __init__(self, limit: int = 4) -> None:
        if type(limit) is not int or not 1 <= limit <= 8:
            raise ValueError("PTZ owner limit must be 1..8")
        self._limit = limit
        self._active: set[str] = set()
        self._lock = threading.Lock()

    @contextmanager
    def reserve(self, camera_id: str) -> Iterator[None]:
        if not camera_id:
            raise ValueError("camera identity is required")
        with self._lock:
            if camera_id in self._active or len(self._active) >= self._limit:
                raise PtzBusy("native PTZ camera/session capacity is busy")
            self._active.add(camera_id)
        try:
            yield
        finally:
            with self._lock:
                self._active.remove(camera_id)
