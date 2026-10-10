"""Bounded connection-local MTP TCP record assembly, not session admission."""

from .media_protocol import mtp_frame_length, verify_mtp_frame
from .mtp_tcp_kcp import TCP_RECORD_LIMIT

MAX_READ_BYTES = 4096
MAX_RECORDS_PER_FEED = 64


class MtpTcpFrameError(ValueError):
    """Terminal framing failure; owner must discard this connection."""


class MtpTcpFramer:
    """One instance per socket, no resynchronization or reconnect reuse.

    Memory/read/batch bounds are local diagnostic policy. The wire length limit
    follows the SDK. Checksum is partial; session identity still needs checking.
    """

    def __init__(self) -> None:
        self._buffer = bytearray()
        self._closed = False

    @property
    def buffered_bytes(self) -> int:
        return len(self._buffer)

    def _fail(self, reason: str) -> None:
        self.abort()
        raise MtpTcpFrameError(reason)

    def feed(self, data: bytes) -> list[bytes]:
        if self._closed:
            raise MtpTcpFrameError("MTP TCP decoder is closed")
        if not isinstance(data, bytes) or len(data) > MAX_READ_BYTES:
            self._fail("MTP TCP read exceeds admission policy")
        self._buffer.extend(data)
        records: list[bytes] = []
        offset = 0
        while len(self._buffer) - offset >= 4:
            prefix = bytes(self._buffer[offset:offset + 4])
            if prefix[0] != 0xC0 or prefix[1] not in (0x10, 0x50, 0x90, 0xD0):
                self._fail("unmapped inbound MTP TCP prefix")
            size = mtp_frame_length(prefix)
            if not 30 <= size <= TCP_RECORD_LIMIT:
                self._fail("invalid MTP TCP record length")
            if len(self._buffer) - offset < size:
                break
            if len(records) >= MAX_RECORDS_PER_FEED:
                self._fail("MTP TCP record batch exceeds admission policy")
            record = bytes(self._buffer[offset:offset + size])
            if not verify_mtp_frame(record):
                self._fail("invalid MTP TCP record checksum")
            records.append(record)
            offset += size
        del self._buffer[:offset]
        return records

    def finish(self) -> None:
        """EOF closes permanently; incomplete records cannot be accepted."""
        if self._closed:
            raise MtpTcpFrameError("MTP TCP decoder is closed")
        if self._buffer:
            self._fail("truncated MTP TCP stream")
        self._closed = True

    def abort(self) -> None:
        """Idempotent local cancellation, not a remote teardown receipt."""
        self._buffer.clear()
        self._closed = True
