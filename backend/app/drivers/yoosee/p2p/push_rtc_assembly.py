"""Bounded offline concatenation of already ordered, plaintext RTC fragments."""

from .push_rtc_fragments import parse_rtc_fragment


class RTCFragmentAssembly:
    """One instance per source/session; never share across cameras or reconnects.

    Local policy: four pending IDs, 256 KiB aggregate payload, 256 records per
    ID. Malformed input/ordering/limit violations close the instance permanently.
    No timeout or scheduler: the owner must close it on cancellation/session
    replacement, or finish it on EOF to detect incomplete assemblies.
    Input must already be ordered; no sequence number is inferred.
    Returned bytes are opaque, NOT validated media or a recursively parsed RTC
    stream. SDK recursion is deliberately not reproduced here.
    """

    def __init__(self) -> None:
        self._pending: dict[int, tuple[bytearray, int]] = {}
        self._size = 0
        self._closed = False

    def close(self) -> None:
        """Discard local fragments on cancellation; idempotent, not remote teardown."""
        self._pending.clear()
        self._size = 0
        self._closed = True

    def finish(self) -> None:
        """Validate EOF and retire this instance, even when fragments are missing.

        Success only means no unfinished fragment IDs remain. It proves neither
        valid media nor a remote hangup acknowledgement.
        """
        if self._closed:
            raise ValueError("RTC fragment assembly is closed")
        incomplete = bool(self._pending)
        self.close()
        if incomplete:
            raise ValueError("truncated RTC fragment stream")

    def feed(self, frame: bytes) -> bytes | None:
        if self._closed:
            raise ValueError("RTC fragment assembly is closed")
        try:
            return self._feed(frame)
        except ValueError:
            self.close()
            raise

    def _feed(self, frame: bytes) -> bytes | None:
        fragment = parse_rtc_fragment(frame)
        identity = fragment.fragment_id
        if fragment.kind == 0xF0:
            if identity in self._pending or len(self._pending) >= 4:
                raise ValueError("RTC fragment begin conflicts with pending state")
            buffer, count = bytearray(), 0
        else:
            if identity not in self._pending:
                raise ValueError("RTC fragment has no matching begin")
            buffer, count = self._pending[identity]
        if fragment.kind == 0xF3:
            self._size -= len(buffer)
            del self._pending[identity]
            return None
        count += 1
        if count > 256 or self._size + len(fragment.payload) > 256 * 1024:
            raise ValueError("RTC fragment assembly limit exceeded")
        buffer.extend(fragment.payload)
        self._size += len(fragment.payload)
        if fragment.kind == 0xF2:
            del self._pending[identity]
            self._size -= len(buffer)
            return bytes(buffer)
        self._pending[identity] = buffer, count
        return None
