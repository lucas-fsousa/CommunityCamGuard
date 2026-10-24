"""Server lifetime for explicit native activations; idle boot, no source scheduler."""

import math
import threading
import time
from collections.abc import Callable

from .native_activation import NativeActivation
from .owned_retirement import OwnedRetirementCoordinator, RetiredIngress
from .source_executor import SourceExecutor
from .source_offer import NativeSourceOffer
from .source_registry import SourceCleanupUnconfirmed, SourceRegistry, SourceUnavailable
from .viewer_gate import ViewerGate


class NativeSourceRuntime:
    def __init__(self, *, viewers: ViewerGate, capacity: int = 1):
        self.sources = SourceRegistry(capacity=capacity)
        self._executor = SourceExecutor(capacity=capacity)
        self._viewers = viewers
        self._capacity = capacity
        self._condition = threading.Condition()
        self._shutdown = threading.Lock()
        self._closed = False
        self._stopped = False
        self._operations = 0
        self._activations: dict[str, NativeActivation] = {}

    def activate(self, *, retirement: OwnedRetirementCoordinator, receipt: RetiredIngress,
                 offer: NativeSourceOffer, apply_routes: Callable[[], None], ready_timeout: float = 5.0):
        if retirement.viewer_gate is not self._viewers or offer.camera_id != receipt.camera_id:
            raise ValueError("matching runtime camera and viewer gate required")
        with self._condition:
            if self._closed or offer.camera_id in self._activations or len(self._activations) >= self._capacity:
                raise SourceUnavailable("native runtime admission unavailable")
            activation = NativeActivation(retirement=retirement, sources=self.sources,
                                          executor=self._executor, apply_routes=apply_routes)
            # Retain before preparation/submission, not after it returns.
            self._activations[offer.camera_id] = activation
            self._operations += 1
        try:
            return activation.start(receipt, offer, ready_timeout=ready_timeout)
        finally:
            with self._condition:
                self._operations -= 1
                self._condition.notify_all()

    def stop(self, *, timeout: float = 30.0) -> None:
        """Off the ASGI loop. Never clear handles after an uncertain shutdown."""
        if isinstance(timeout, bool) or not math.isfinite(timeout) or not 0 < timeout <= 30:
            raise ValueError("bounded native runtime shutdown timeout required")
        deadline = time.monotonic() + timeout
        if not self._shutdown.acquire(timeout=timeout):
            raise SourceCleanupUnconfirmed("native runtime shutdown already pending")
        try:
            self._stop(deadline)
        finally:
            self._shutdown.release()

    def _stop(self, deadline: float) -> None:
        with self._condition:
            if self._stopped:
                return
            self._closed = True
            activations = tuple(self._activations.values())
        self._executor.shutdown(wait=False)  # close admission before invoking callbacks
        for activation in activations:
            activation.request_stop()
        failed = False
        try:
            self.sources.begin_shutdown()
        except SourceCleanupUnconfirmed:
            failed = True
        self._executor.shutdown(wait=False, cancel_futures=True)  # no queued factory survives stop
        with self._condition:
            while self._operations:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    failed = True
                    break
                self._condition.wait(remaining)
            operations_finished = self._operations == 0
        if operations_finished:
            for activation in activations:
                if time.monotonic() >= deadline:
                    failed = True
                    break
                try:
                    if activation.session is not None:
                        activation.session.close(timeout=deadline - time.monotonic())
                except Exception:
                    failed = True  # still attempt every other retained owner
        try:
            self._executor.stop(timeout=max(0.0, deadline - time.monotonic()))
        except SourceCleanupUnconfirmed:
            failed = True
        if failed:
            raise SourceCleanupUnconfirmed("native runtime shutdown unconfirmed") from None
        with self._condition:
            self._stopped = True
            self._activations.clear()
