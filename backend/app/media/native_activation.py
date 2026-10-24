"""Explicit post-retirement native preparation; no default service or source API."""

import math
import threading
import time
from collections.abc import Callable
from concurrent.futures import Executor

from .mp4_codec import h264_mime
from .mp4_fanout import StreamClosedError
from .owned_retirement import OwnedRetirementCoordinator, RetiredIngress
from .prepared_native import PreparedNative
from .source_offer import NativeSourceOffer
from .source_registry import SourceRegistry, SourceUnavailable


def ready_video(sources: SourceRegistry, camera_id: str, owner: object) -> bool:
    """Probe only shared local output, never open a second camera/encoder."""
    try:
        reader = sources.subscribe(camera_id, owner=owner)
    except (SourceUnavailable, StreamClosedError):
        return False
    try:
        initialization, fragment = reader.take(), reader.take()
        if initialization is None or not initialization.initialization:
            return False
        h264_mime(initialization.data)
        return fragment is not None and not fragment.initialization and bool(fragment.data)
    except StreamClosedError:
        return False
    finally:
        reader.close()


class NativeActivation:
    def __init__(self, *, retirement: OwnedRetirementCoordinator, sources: SourceRegistry,
                 executor: Executor, apply_routes: Callable[[], None]):
        self._retirement, self._sources = retirement, sources
        self._executor, self._apply_routes = executor, apply_routes
        self._claim = threading.Lock()
        self._used = False
        self.session: PreparedNative | None = None  # retain ownership even after failure

    def start(self, receipt: RetiredIngress, offer: NativeSourceOffer, *,
              ready_timeout: float = 5.0) -> PreparedNative:
        if (isinstance(ready_timeout, bool) or not math.isfinite(ready_timeout)
                or not 0 < ready_timeout <= 30):
            raise ValueError("bounded native preparation timeout required")
        if (offer.camera_id != receipt.camera_id or receipt.selection.mode != "native"
                or receipt.selection.phase != "transition" or offer.valid() is not True):
            raise SourceUnavailable("reviewed native target required")
        with self._claim:
            if self._used:
                raise SourceUnavailable("native activation already attempted")
            self._used = True
        with self._retirement.claim(receipt):
            session = PreparedNative(offer=offer, selection=receipt.selection, sources=self._sources,
                                     viewers=self._retirement.viewer_gate)
            self.session = session
            try:
                future = session.launch(self._executor)
                deadline = time.monotonic() + ready_timeout
                while not future.done():
                    if ready_video(self._sources, offer.camera_id, session.owner):
                        session.publish(self._apply_routes)
                        return session
                    if time.monotonic() >= deadline:
                        raise SourceUnavailable("native video readiness unavailable")
                    time.sleep(0.02)
                raise SourceUnavailable("native source ended before readiness")
            except BaseException:
                session.close()  # uncertain cleanup propagates; session/handles retained
                raise
