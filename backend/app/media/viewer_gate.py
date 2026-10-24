"""Bounded signaling/MSE admission and drain; never a WebRTC/ingress receipt."""

import math
import threading
import time
from dataclasses import dataclass, field

from ..camera_identity import valid_camera_id
from .source_registry import SourceCleanupUnconfirmed, SourceUnavailable


@dataclass(eq=False)
class ViewerLease:
    camera_id: str
    cancelled: threading.Event = field(default_factory=threading.Event, repr=False)


class ViewerGate:
    def __init__(self, *, capacity: int = 64):
        if type(capacity) is not int or not 1 <= capacity <= 256:
            raise ValueError("viewer capacity must be between 1 and 256")
        self._capacity = capacity
        self._condition = threading.Condition()
        self._leases: set[ViewerLease] = set()
        self._holds: dict[str, str] = {}

    @staticmethod
    def _validate(camera_id: str) -> None:
        if type(camera_id) is not str or not valid_camera_id(camera_id):
            raise ValueError("public camera ID required")

    def admit(self, camera_id: str) -> ViewerLease:
        self._validate(camera_id)
        with self._condition:
            if camera_id in self._holds or len(self._leases) >= self._capacity:
                raise SourceUnavailable("viewer admission unavailable")
            lease = ViewerLease(camera_id)
            self._leases.add(lease)
            return lease

    def release(self, lease: ViewerLease) -> None:
        """Only after the caller has reaped upstream/tasks. Safe to repeat."""
        with self._condition:
            self._leases.discard(lease)
            self._condition.notify_all()

    def is_suspended(self, camera_id: str) -> bool:
        self._validate(camera_id)
        with self._condition:
            return camera_id in self._holds

    def is_drained(self, camera_id: str) -> bool:
        self._validate(camera_id)
        with self._condition:
            return (self._holds.get(camera_id) == "drained"
                    and not any(lease.camera_id == camera_id for lease in self._leases))

    def hold(self, camera_id: str) -> None:
        """Nonblocking failure latch; no drain receipt or implicit resume."""
        self._validate(camera_id)
        with self._condition:
            if camera_id not in self._holds:
                if len(self._holds) >= 32:
                    raise SourceUnavailable("viewer suspension capacity reached")
                self._holds[camera_id] = "blocked"
            for lease in self._leases:
                if lease.camera_id == camera_id:
                    lease.cancelled.set()

    def suspend(self, camera_id: str, *, timeout: float = 8.0) -> None:
        """Blocking coordinator-thread operation, NEVER run on the ASGI loop.

        Close admission before cancellation; a failed wait retains the hold.
        Successful return certifies only registered operations have cleaned up.
        """
        self._validate(camera_id)
        if isinstance(timeout, bool) or not math.isfinite(timeout) or not 0 < timeout <= 30:
            raise ValueError("bounded viewer drain timeout required")
        deadline = time.monotonic() + timeout
        with self._condition:
            if self._holds.get(camera_id) == "draining":
                raise SourceUnavailable("viewer drain already in progress")
            if camera_id not in self._holds and len(self._holds) >= 32:
                raise SourceUnavailable("viewer suspension capacity reached")
            self._holds[camera_id] = "draining"
            try:
                for lease in self._leases:
                    if lease.camera_id == camera_id:
                        lease.cancelled.set()
                while any(lease.camera_id == camera_id for lease in self._leases):
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise SourceCleanupUnconfirmed("viewer drain unconfirmed")
                    self._condition.wait(remaining)
            except BaseException:
                self._holds[camera_id] = "blocked"
                raise
            self._holds[camera_id] = "drained"

    def resume(self, camera_id: str) -> None:
        self._validate(camera_id)
        with self._condition:
            if camera_id not in self._holds:
                return
            if self._holds[camera_id] != "drained" or any(lease.camera_id == camera_id for lease in self._leases):
                raise SourceCleanupUnconfirmed("viewer suspension unconfirmed")
            del self._holds[camera_id]
