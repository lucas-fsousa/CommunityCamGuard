"""Bounded, socket-free TCP framing for SDK 6.45 push messages.

Framing is not authentication: callers must still validate message-specific body,
checksum and session identity against an authenticated peer before using a frame.
"""

import struct

HEADER_SIZE = 20
MAX_FRAME_BYTES = 0x8400  # iv_on_push_rcv_tcp_data's total-frame ceiling.
MAX_FEED_BYTES = MAX_FRAME_BYTES  # Local read/admission policy.
MAX_FRAMES_PER_FEED = 256  # Local CPU/output-allocation policy.


class PushFrameError(ValueError):
    """Malformed or over-budget stream; discard this decoder and its connection."""


class PushTcpFramer:
    """One decoder per connection, permanently closed on error or EOF.

    Accept bounded reads; keep at most one incomplete frame between calls. No
    magic-byte resynchronization or reuse across reconnects: stale session bytes
    must never become a new connection's certification response.
    """

    def __init__(self) -> None:
        self._buffer = bytearray()
        self._closed = False

    @property
    def buffered_bytes(self) -> int:
        return len(self._buffer)

    def _fail(self, reason: str) -> None:
        self._buffer.clear()
        self._closed = True
        raise PushFrameError(reason)

    def feed(self, data: bytes) -> list[bytes]:
        if self._closed:
            raise PushFrameError("push decoder is closed")
        if not isinstance(data, bytes) or len(data) > MAX_FEED_BYTES:
            self._fail("push read exceeds admission policy")
        self._buffer.extend(data)
        frames: list[bytes] = []
        offset = 0
        while len(self._buffer) - offset >= HEADER_SIZE:
            if self._buffer[offset] != 3:
                self._fail("invalid push protocol")
            size = HEADER_SIZE + struct.unpack_from("<H", self._buffer, offset + 4)[0]
            if not HEADER_SIZE < size <= MAX_FRAME_BYTES:
                self._fail("invalid push frame length")
            if len(self._buffer) - offset < size:
                break
            if len(frames) >= MAX_FRAMES_PER_FEED:
                self._fail("push frame batch exceeds admission policy")
            frames.append(bytes(self._buffer[offset:offset + size]))
            offset += size
        del self._buffer[:offset]
        return frames

    def finish(self) -> None:
        """Mark EOF; partial headers/bodies are errors, never accepted as frames."""
        if self._closed:
            raise PushFrameError("push decoder is closed")
        if self._buffer:
            self._fail("truncated push stream")
        self._closed = True

    def abort(self) -> None:
        """Discard partial input on local cancellation; idempotent, unlike EOF."""
        self._buffer.clear()
        self._closed = True
