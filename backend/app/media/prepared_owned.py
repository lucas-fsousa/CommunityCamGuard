"""One retained RTSP worker; publication follows a verified retirement claim."""

import math
import threading
import time
from collections.abc import Callable
from concurrent.futures import CancelledError, Future, TimeoutError

from ..db import source_policy
from ..recording.ingress_gate import RecordingIngressGate
from .owned_bindings import OwnedIngressBindings
from .owned_retirement import OwnedRun
from .retirement_wait import locked_until, remaining
from .rtsp_ingress import OwnedRtspIngress
from .source_executor import SourceExecutor
from .source_registry import SourceCleanupUnconfirmed, SourceUnavailable
from .viewer_gate import ViewerGate


class PreparedOwned:
    def __init__(self, worker: OwnedRtspIngress, selection: source_policy.Selection, *,
                 viewers: ViewerGate, recordings: RecordingIngressGate, bindings: OwnedIngressBindings):
        self.worker, self.selection = worker, selection
        self._viewers, self._recordings, self._bindings = viewers, recordings, bindings
        self._lock, self._closing = threading.Lock(), threading.Lock()
        self._stop = threading.Event()
        self._closed = False
        self.authorized = False
        self.completion: Future | None = None

    def request_stop(self) -> None:
        self._stop.set()
        self.worker.stop()

    def launch(self, executor: SourceExecutor) -> Future:
        with self._lock:
            if self._stop.is_set() or self.completion is not None:
                raise SourceUnavailable("owned source launch unavailable")
            self.completion = executor.submit(self.worker.run, cancelled=self._stop.is_set)
            completion = self.completion
        completion.add_done_callback(self._ended)
        return completion

    @property
    def running(self) -> OwnedRun:
        if self.completion is None:
            raise SourceUnavailable("owned source has not been submitted")
        return OwnedRun(self.worker, self.completion)

    def _hold_current_locked(self) -> bool:
        camera_id = self.worker.camera_id
        # Retain the read transaction through the hold. An old completion must
        # never hold consumers after a newer revision has already been published.
        with source_policy.snapshot((camera_id,)) as selections:
            current = selections[camera_id] == self.selection
            if current:
                self._viewers.hold(camera_id)
                self._bindings.withdraw(camera_id, worker=self.worker)
        if current and self.selection.phase != "blocked":
            try:
                self.selection = source_policy.block(camera_id, expected_revision=self.selection.revision)
            except source_policy.SourcePolicyConflict:
                pass  # another coordinator owns the newer intent
        return current

    def _ended(self, _future: Future) -> None:
        with self._lock:
            if not self._closed:
                try:
                    self._hold_current_locked()
                except Exception:
                    self._stop.set()  # keep handles; shutdown will report failed cleanup

    def publish(self, apply_routes: Callable[[], None]) -> None:
        with self._lock:
            self._check_live()
            self.selection = source_policy.activate(self.worker.camera_id, expected_revision=self.selection.revision)
            self._bindings.attach(self.worker)
            apply_routes()
            self._check_live()
            self._recordings.resume(self.worker.camera_id)
            self._viewers.resume(self.worker.camera_id)

    def _check_live(self) -> None:
        if (self._stop.is_set() or self.completion is None or self.completion.done()
                or self.worker.pid is None or self.worker.restream_url is None):
            raise SourceUnavailable("owned RTSP source is not ready")

    def close(self, *, timeout: float = 15) -> None:
        if isinstance(timeout, bool) or not math.isfinite(timeout) or not 0 < timeout <= 30:
            raise ValueError("bounded owned cleanup timeout required")
        self.request_stop()
        deadline = time.monotonic() + timeout
        with locked_until(self._closing, deadline):
            with locked_until(self._lock, deadline):
                if self._closed:
                    return
                current = self.authorized and self._hold_current_locked()
            # No state lock across waits: Future callbacks need it to finish.
            failure = None
            if current:
                try:
                    self._viewers.suspend(self.worker.camera_id, timeout=min(8, remaining(deadline)))
                    self._recordings.suspend(self.worker.camera_id)
                except Exception as exc:
                    failure = exc
            if self.completion is not None:
                try:
                    self.completion.result(timeout=min(6, remaining(deadline)))
                except CancelledError:
                    pass  # queued callable never ran
                except TimeoutError:
                    raise SourceCleanupUnconfirmed("owned source run still pending") from None
                except Exception:
                    if self.worker.pid is not None:
                        raise SourceCleanupUnconfirmed("owned child retirement unconfirmed") from None
            if self.worker.pid is not None or failure is not None:
                raise SourceCleanupUnconfirmed("owned source cleanup unconfirmed") from None
            if self.authorized:
                self._bindings.discard(self.worker.camera_id, worker=self.worker)
            with locked_until(self._lock, deadline):
                self._closed = True
