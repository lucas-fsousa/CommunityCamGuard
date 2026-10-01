"""Connection-scoped offline reception; no sockets, retries or authentication."""

from .push_framing import PushFrameError, PushTcpFramer


class PushReception:
    """Single-owner/event-loop receive state with opaque callback generations.

    Each future socket callback must capture the token returned by begin(), never
    look up a mutable 'current token' when it runs. This prevents old read/EOF
    events from feeding or closing a replacement decoder. Not a cross-thread lock
    or remote identity check. Frames still require per-message validation.
    """

    def __init__(self) -> None:
        self._generation: object | None = None
        self._decoder: PushTcpFramer | None = None

    def begin(self) -> object:
        """Retire previous local state and start a new, unauthenticated stream."""
        self.cancel()
        self._generation = object()
        self._decoder = PushTcpFramer()
        return self._generation

    def cancel(self) -> None:
        """Invalidate callbacks and discard bytes; caller still owns socket cleanup."""
        self._generation = None
        if self._decoder is not None:
            self._decoder.abort()
        self._decoder = None

    def receive(self, generation: object, data: bytes) -> list[bytes]:
        if generation is not self._generation or self._decoder is None:
            return []
        try:
            return self._decoder.feed(data)
        except PushFrameError:
            self.cancel()
            raise

    def eof(self, generation: object) -> None:
        if generation is not self._generation or self._decoder is None:
            return
        try:
            self._decoder.finish()
        finally:
            self.cancel()
