"""Retain partial startup services; release the server owner only after all stop."""

import threading
from contextlib import ExitStack
from typing import Protocol, TypeVar

from ..media.server_owner import ServerOwner
from ..media.source_registry import SourceCleanupUnconfirmed, SourceUnavailable


class Stoppable(Protocol):
    def stop(self) -> None: ...


T = TypeVar("T", bound=Stoppable)


class ServiceLifetime:
    def __init__(self, owner: ServerOwner):
        if not owner.acquired:
            raise SourceUnavailable("server ownership required")
        self.owner = owner
        self._services: list[Stoppable] = []
        self._lock = threading.Lock()
        self._shutdown = threading.Lock()
        self._closing = False

    def own(self, service: T) -> T:
        with self._lock:
            if self._closing:
                raise SourceUnavailable("service ownership closed")
            self._services.append(service)
        return service

    def stop(self) -> None:
        if not self._shutdown.acquire(timeout=30):
            raise SourceCleanupUnconfirmed("server cleanup already pending")
        try:
            with self._lock:
                self._closing = True
                services = tuple(self._services)
            with ExitStack() as cleanup:
                for service in services:
                    cleanup.callback(service.stop)
            # On any stop failure, keep both service handles and the OS lock.
            self.owner.close()
            with self._lock:
                self._services.clear()
        finally:
            self._shutdown.release()
