"""One bounded MP4 epoch shared by consumers; no encoder, camera or HTTP route.

Ingest only the trusted local frag_keyframe muxer's output. Caller authenticates
each subscription and closes it on revocation. References handed to callers are
outside this ring's budget: each transport must bound its own pending writes.
"""

import threading
from collections import deque

from .fragmented_mp4 import MAX_BYTES, MAX_CHUNKS, Mp4Chunk

MAX_READERS = 16
MAX_INIT_BYTES = 256 * 1024


class StreamClosedError(RuntimeError):
    pass


class SlowConsumerError(StreamClosedError):
    pass


class Subscription:
    def __init__(self, hub: "Mp4Fanout", sequence: int):
        self._hub = hub
        self._sequence = sequence
        self._initialization_pending = True
        self._error: type[StreamClosedError] | None = None

    def take(self) -> Mp4Chunk | None:
        return self._hub._take(self)

    def close(self) -> None:
        with self._hub._lock:
            self._hub._readers.discard(self)
            self._error = self._error or StreamClosedError


class Mp4Fanout:
    """Retain one init plus <=8 fragments within2MiB, with at most16 cursors.

    No callback, blocking I/O or waiting for a reader runs under the lock. Joining
    begins at the newest complete keyframe fragment, not at historical backlog.
    Decoder replacement needs a new hub; an old epoch can never receive new init.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._initialization: Mp4Chunk | None = None
        self._fragments: deque[tuple[int, Mp4Chunk]] = deque()
        self._readers: set[Subscription] = set()
        self._bytes = 0
        self._sequence = 0
        self._closed = False

    @property
    def buffered_bytes(self) -> int:
        with self._lock:
            return self._bytes

    @property
    def reader_count(self) -> int:
        with self._lock:
            return len(self._readers)

    def publish(self, chunk: Mp4Chunk) -> None:
        with self._lock:
            if self._closed:
                raise StreamClosedError("native output epoch ended")
            try:
                if (type(chunk.data) is not bytes or not chunk.data
                        or type(chunk.initialization) is not bool):
                    raise ValueError("invalid MP4 chunk")
                if chunk.initialization:
                    if self._initialization is not None or len(chunk.data) > MAX_INIT_BYTES:
                        raise ValueError("invalid MP4 initialization")
                    self._initialization = chunk
                    self._bytes = len(chunk.data)
                    return
                if self._initialization is None:
                    raise ValueError("MP4 initialization required")
                if len(chunk.data) + len(self._initialization.data) > MAX_BYTES:
                    raise ValueError("MP4 fragment exceeds ring budget")
                while self._fragments and (len(self._fragments) >= MAX_CHUNKS
                                           or self._bytes + len(chunk.data) > MAX_BYTES):
                    _, removed = self._fragments.popleft()
                    self._bytes -= len(removed.data)
                self._fragments.append((self._sequence, chunk))
                self._sequence += 1
                self._bytes += len(chunk.data)
                first = self._fragments[0][0]
                for reader in tuple(self._readers):
                    if reader._sequence < first:
                        reader._error = SlowConsumerError
                        self._readers.remove(reader)
            except Exception:
                self._close()
                raise

    def subscribe(self) -> Subscription:
        with self._lock:
            if self._closed or not self._fragments:
                raise StreamClosedError("native output is not ready")
            if len(self._readers) >= MAX_READERS:
                raise StreamClosedError("native output reader capacity reached")
            reader = Subscription(self, self._fragments[-1][0])
            self._readers.add(reader)
            return reader

    def _take(self, reader: Subscription) -> Mp4Chunk | None:
        with self._lock:
            if reader._error is not None:
                raise reader._error("native output subscription ended")
            if reader not in self._readers or self._closed:
                raise StreamClosedError("native output subscription ended")
            if reader._initialization_pending:
                reader._initialization_pending = False
                return self._initialization
            index = reader._sequence - self._fragments[0][0]
            if index >= len(self._fragments):
                return None
            chunk = self._fragments[index][1]
            reader._sequence += 1
            return chunk

    def _close(self) -> None:
        self._closed = True
        for reader in self._readers:
            reader._error = StreamClosedError
        self._readers.clear()
        self._fragments.clear()
        self._initialization = None
        self._bytes = 0

    def close(self) -> None:
        with self._lock:
            self._close()
