"""Small ciphertext queue while paired AV channels await correlated ACCEPT."""

from collections.abc import Callable

from .kcp_receive import ReceiveError

MAX_PENDING_BYTES = 256 * 1024
MAX_PENDING_MESSAGES = 128
PENDING_SECONDS = 1.0


class PendingAv:
    def __init__(self, clock: Callable[[], float]) -> None:
        self._clock = clock
        self._since: float | None = None
        self._messages: list[bytes] = []
        self.buffered_bytes = 0

    def clear(self) -> None:
        self._messages.clear()
        self.buffered_bytes = 0
        self._since = None

    def poll(self) -> None:
        if self._since is not None and self._clock() - self._since >= PENDING_SECONDS:
            self.clear()
            raise ReceiveError("AV cross-channel acceptance deadline exceeded")

    def append(self, message: bytes) -> None:
        self.poll()
        if (len(self._messages) >= MAX_PENDING_MESSAGES
                or self.buffered_bytes + len(message) > MAX_PENDING_BYTES):
            self.clear()
            raise ReceiveError("AV cross-channel receive budget exceeded")
        if self._since is None:
            self._since = self._clock()
        self._messages.append(message)
        self.buffered_bytes += len(message)

    def take(self) -> tuple[bytes, ...]:
        self.poll()
        messages = tuple(self._messages)
        self.clear()
        return messages
