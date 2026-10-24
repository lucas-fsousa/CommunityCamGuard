"""Server-owned, explicitly prepared RTSP runs. Never migrates a legacy hub."""

import math
import threading
import time
from collections.abc import Callable

from ..recording.ingress_gate import RecordingIngressGate
from .owned_bindings import OwnedIngressBindings
from .owned_retirement import OwnedRetirementCoordinator, OwnedRun, RetiredIngress
from .prepared_owned import PreparedOwned
from .rtsp_ingress import OwnedRtspIngress
from .source_executor import SourceExecutor
from .source_registry import SourceCleanupUnconfirmed, SourceUnavailable
from .viewer_gate import ViewerGate


class OwnedSourceRuntime:
    def __init__(self, *, viewers: ViewerGate, recordings: RecordingIngressGate,
                 bindings: OwnedIngressBindings, capacity: int = 1):
        self.retirement = OwnedRetirementCoordinator(viewers=viewers, recordings=recordings, bindings=bindings)
        self._viewers, self._recordings, self._bindings = viewers, recordings, bindings
        self._executor = SourceExecutor(capacity=capacity)
        self._capacity = capacity
        self._condition = threading.Condition()
        self._shutdown = threading.Lock()
        self._closed = False
        self._stopped = False
        self._operations = 0
        self._runs: dict[str, PreparedOwned] = {}

    def activate(self, receipt: RetiredIngress, worker: OwnedRtspIngress, *,
                 apply_routes: Callable[[], None], ready_timeout: float = 5) -> OwnedRun:
        if (isinstance(ready_timeout, bool) or not math.isfinite(ready_timeout)
                or not 0 < ready_timeout <= 30):
            raise ValueError("bounded owned preparation timeout required")
        if (not isinstance(worker, OwnedRtspIngress) or worker.camera_id != receipt.camera_id
                or receipt.selection.mode != "owned_rtsp" or receipt.selection.phase != "transition"
                or worker.pid is not None or worker.restream_url is not None):
            raise SourceUnavailable("matching retired owned target required")
        with self._condition:
            if self._closed or worker.camera_id in self._runs or len(self._runs) >= self._capacity:
                raise SourceUnavailable("owned runtime admission unavailable")
            prepared = PreparedOwned(worker, receipt.selection, viewers=self._viewers,
                                     recordings=self._recordings, bindings=self._bindings)
            self._runs[worker.camera_id] = prepared
            self._operations += 1
        try:
            with self.retirement.claim(receipt):
                prepared.authorized = True
                future = prepared.launch(self._executor)
                deadline = time.monotonic() + ready_timeout
                while not future.done():
                    if self._closed or time.monotonic() >= deadline:
                        raise SourceUnavailable("owned preparation cancelled or timed out")
                    if worker.pid is not None and worker.restream_url is not None:
                        prepared.publish(apply_routes)
                        return prepared.running
                    time.sleep(0.02)
                raise SourceUnavailable("owned worker ended before readiness")
        except BaseException:
            prepared.close()  # fail closed, retain the prepared run on uncertainty
            raise
        finally:
            with self._condition:
                self._operations -= 1
                self._condition.notify_all()

    def stop(self, *, timeout: float = 30) -> None:
        if isinstance(timeout, bool) or not math.isfinite(timeout) or not 0 < timeout <= 30:
            raise ValueError("bounded owned shutdown timeout required")
        deadline = time.monotonic() + timeout
        if not self._shutdown.acquire(timeout=timeout):
            raise SourceCleanupUnconfirmed("owned runtime shutdown already pending")
        try:
            self._stop(deadline)
        finally:
            self._shutdown.release()

    def _stop(self, deadline: float) -> None:
        with self._condition:
            if self._stopped:
                return
            self._closed = True
            prepared = tuple(self._runs.values())
        self._executor.shutdown(wait=False)
        for session in prepared:
            session.request_stop()
        self._executor.shutdown(wait=False, cancel_futures=True)
        failed = False
        with self._condition:
            while self._operations:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    failed = True
                    break
                self._condition.wait(remaining)
            operations_finished = self._operations == 0
        if operations_finished:
            for session in prepared:
                if time.monotonic() >= deadline:
                    failed = True
                    break
                try:
                    session.close(timeout=deadline - time.monotonic())
                except Exception:
                    failed = True
        try:
            self._executor.stop(timeout=max(0, deadline - time.monotonic()))
        except SourceCleanupUnconfirmed:
            failed = True
        if failed:
            raise SourceCleanupUnconfirmed("owned runtime shutdown unconfirmed") from None
        with self._condition:
            self._stopped = True
            self._runs.clear()
