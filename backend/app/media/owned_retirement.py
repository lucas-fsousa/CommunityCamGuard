"""Verified retirement half of a source handoff, for an owned RTSP ingress only.

No factory, activation, fallback, driver capability inference or legacy hub stop.
Run on a coordinator thread, never the ASGI event loop. Failures stay latched.
"""

import math
import threading
from collections.abc import Iterator
from concurrent.futures import Future
from contextlib import contextmanager
from dataclasses import dataclass, field

from ..db import source_policy
from ..recording.ingress_gate import RecordingIngressGate
from .owned_bindings import OwnedIngressBindings
from .rtsp_ingress import OwnedRtspIngress
from .source_registry import SourceCleanupUnconfirmed, SourceUnavailable
from .viewer_gate import ViewerGate


@dataclass(frozen=True)
class OwnedRun:
    worker: OwnedRtspIngress = field(repr=False)
    completion: Future = field(repr=False)


@dataclass(frozen=True)
class RetiredIngress:
    """Report of local retirement, NOT authority to construct a new source."""
    camera_id: str
    selection: source_policy.Selection


class OwnedRetirementCoordinator:
    def __init__(self, *, viewers: ViewerGate, recordings: RecordingIngressGate,
                 bindings: OwnedIngressBindings):
        self._viewers = viewers
        self._recordings = recordings
        self._bindings = bindings
        self._operation = threading.Lock()
        self._failed: OwnedRun | None = None  # retain owned handles on uncertainty
        self._receipts: dict[str, RetiredIngress] = {}

    @property
    def viewer_gate(self) -> ViewerGate:
        return self._viewers

    @contextmanager
    def claim(self, receipt: RetiredIngress) -> Iterator[None]:
        """One-use internal preparation permit, never accept serialized receipts."""
        if not self._operation.acquire(blocking=False):
            raise SourceUnavailable("source handoff already in progress")
        try:
            if (self._failed is not None or self._receipts.get(receipt.camera_id) is not receipt
                    or not self._viewers.is_drained(receipt.camera_id)
                    or not self._recordings.is_retired(receipt.camera_id)
                    or source_policy.read_for((receipt.camera_id,))[receipt.camera_id] != receipt.selection):
                raise SourceUnavailable("current confirmed retirement required")
            del self._receipts[receipt.camera_id]
            yield
        finally:
            self._operation.release()

    def retire(self, running: OwnedRun, *, target_mode: str, expected_revision: int,
               drain_timeout: float = 8.0) -> RetiredIngress:
        if target_mode not in {"native", "owned_rtsp"}:
            raise ValueError("owned or native target intent required")
        if (isinstance(drain_timeout, bool) or not math.isfinite(drain_timeout)
                or not 0 < drain_timeout <= 30):
            raise ValueError("bounded viewer drain timeout required")
        if not self._operation.acquire(blocking=False):
            raise SourceUnavailable("source retirement already in progress")
        try:
            if self._failed is not None:
                raise SourceCleanupUnconfirmed("source retirement recovery required")
            if len(self._receipts) >= 32:
                raise SourceUnavailable("pending retirement capacity reached")
            camera_id = running.worker.camera_id
            previous = source_policy.read_for((camera_id,))[camera_id]
            binding = self._bindings.binding(camera_id, previous)
            if binding is None or binding.worker is not running.worker:
                # Never treat shared-go2rtc DELETE, a missing binding or another
                # generation's Future as an old-source ownership receipt.
                raise SourceUnavailable("matching owned ingress required")
            if self._viewers.is_suspended(camera_id) or self._recordings.is_suspended(camera_id):
                raise SourceUnavailable("consumer admission already held")
            pending = source_policy.begin_change(camera_id, target_mode,
                                                 expected_revision=expected_revision)
            try:
                self._viewers.suspend(camera_id, timeout=drain_timeout)
                self._recordings.suspend(camera_id)
                self._bindings.withdraw(camera_id)
                running.worker.stop()
                running.completion.result(timeout=6)
                if running.worker.pid is not None:
                    raise SourceCleanupUnconfirmed("owned ingress reap unconfirmed")
                self._bindings.discard(camera_id)
                if source_policy.read_for((camera_id,))[camera_id] != pending:
                    raise SourceCleanupUnconfirmed("source intent changed during retirement")
                # Leave both holds and transition intent in place. A later
                # driver-aware activation must prepare/apply before releasing.
                receipt = RetiredIngress(camera_id, pending)
                self._receipts[camera_id] = receipt
                return receipt
            except BaseException as exc:
                self._failed = running
                try:
                    source_policy.block(camera_id, expected_revision=pending.revision)
                except Exception:
                    pass  # never overwrite a newer revision or hide local failure
                if not isinstance(exc, Exception):
                    raise
                raise SourceCleanupUnconfirmed("source retirement incomplete; replacement blocked") from None
        finally:
            self._operation.release()
