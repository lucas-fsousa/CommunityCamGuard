"""One owned readiness waiter: cold camera startup cannot block ASGI or expire its source."""

import logging
import threading
from collections.abc import Callable

from .prepared_native import PreparedNative
from .source_registry import SourceCleanupUnconfirmed

log = logging.getLogger(__name__)


class NativePublication:
    def __init__(self, session: PreparedNative, *, ready: Callable[[], bool],
                 publish: Callable[[], None], cancelled: Callable[[], bool]):
        self._session, self._ready, self._publish, self._cancelled = session, ready, publish, cancelled
        self._wake = threading.Event()
        self._thread = threading.Thread(target=self._run, name='native-readiness', daemon=True)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._wake.set()

    def join(self, timeout: float) -> None:
        if self._thread.ident is not None:
            self._thread.join(timeout=max(0, timeout))
            if self._thread.is_alive():
                raise SourceCleanupUnconfirmed('native readiness shutdown unconfirmed')

    def _run(self) -> None:
        try:
            while not self._wake.is_set() and not self._cancelled():
                future = self._session.completion
                if future is None or future.done():
                    return
                if self._ready():
                    if not self._cancelled() and not self._wake.is_set():
                        self._publish()
                    return
                self._wake.wait(0.1)
        except BaseException as error:
            log.warning('native readiness failed camera_id=%s error_type=%s',
                        self._session.offer.camera_id, type(error).__name__)
            self._session.request_stop()
            try:
                self._session.close()
            except Exception:
                pass  # Retained session/runtime still enforce confirmed shutdown.
