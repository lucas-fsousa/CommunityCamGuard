"""Bounded runtime bindings for already-owned, already-ready RTSP workers.

No source factory, thread, retry or default instance. This does not retire legacy
ingress or grant admission: the handoff coordinator must do that before attach.
"""

import threading
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field

from ..db import registry, source_policy
from .rtsp_ingress import OwnedRtspIngress
from .source_registry import SourceCleanupUnconfirmed, SourceUnavailable


@dataclass(frozen=True, eq=False)
class BoundIngress:
    camera_id: str
    revision: int
    worker: OwnedRtspIngress = field(repr=False)
    withdrawn: threading.Event = field(default_factory=threading.Event, repr=False)

    @property
    def url(self) -> str | None:
        if self.withdrawn.is_set() or self.worker.pid is None:
            return None
        return self.worker.restream_url

    def matches(self, camera_id: str, selection: source_policy.Selection) -> bool:
        return (self.camera_id == camera_id and self.revision == selection.revision
                and selection.mode == "owned_rtsp" and selection.phase == "active"
                and self.url is not None)


class OwnedIngressBindings:
    def __init__(self, *, capacity: int = 1):
        if type(capacity) is not int or not 1 <= capacity <= 32:
            raise ValueError("owned ingress capacity must be between 1 and 32")
        self._capacity = capacity
        self._lock = threading.Lock()
        self._entries: dict[str, BoundIngress] = {}

    def attach(self, worker: OwnedRtspIngress) -> BoundIngress:
        if not isinstance(worker, OwnedRtspIngress):
            raise ValueError("owned RTSP worker required")
        if registry.get_camera_by_id(worker.camera_id) is None:
            raise SourceUnavailable("configured camera required")
        with source_policy.snapshot((worker.camera_id,)) as selections, self._lock:
            selection = selections[worker.camera_id]
            if selection.mode != "owned_rtsp" or selection.phase != "active":
                raise SourceUnavailable("owned source selection is not active")
            if worker.camera_id in self._entries or len(self._entries) >= self._capacity:
                raise SourceUnavailable("owned ingress binding already reserved")
            binding = BoundIngress(worker.camera_id, selection.revision, worker)
            if binding.url is None:
                raise SourceUnavailable("owned RTSP worker is not ready")
            self._entries[worker.camera_id] = binding
            return binding

    def binding(self, camera_id: str, selection: source_policy.Selection) -> BoundIngress | None:
        with self.admission(camera_id, selection) as binding:
            return binding

    @contextmanager
    def admission(self, camera_id: str, selection: source_policy.Selection) -> Iterator[BoundIngress | None]:
        """Retain this through a consumer birth; withdrawal waits for admission."""
        with self._lock:
            binding = self._entries.get(camera_id)
            yield binding if binding is not None and binding.matches(camera_id, selection) else None

    @contextmanager
    def snapshot(self) -> Iterator[dict[str, BoundIngress]]:
        """Keep this lock through actual config write, after taking policy snapshot."""
        with self._lock:
            yield dict(self._entries)

    def withdraw(self, camera_id: str) -> bool:
        """Hide delivery immediately, retaining ownership until the child is reaped."""
        with self._lock:
            binding = self._entries.get(camera_id)
            if binding is None:
                return False
            binding.withdrawn.set()
            return True

    def discard(self, camera_id: str) -> None:
        with self._lock:
            binding = self._entries.get(camera_id)
            if binding is None:
                return
            if binding.worker.pid is not None:
                raise SourceCleanupUnconfirmed("owned RTSP worker has not been reaped")
            binding.withdrawn.set()
            del self._entries[camera_id]
