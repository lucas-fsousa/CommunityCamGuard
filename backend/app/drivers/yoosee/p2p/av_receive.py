"""Experimental inbound AV negotiation and continuous V1 parsing; no socket I/O.

Use only with a freshly negotiated, externally authenticated route. This consumes
ACCEPT but does not send INIT or implement its reliable outbound state. Production
intercom and AV initialization deliberately do not use this module yet.
"""

from __future__ import annotations

import struct
import time
from collections.abc import Callable
from dataclasses import dataclass, field

from .kcp_receive import ReceiveError
from .receive_lifecycle import ReceiveLifecycle
from .stream_protocol import V1EncodingHeader, decrypt_media_tlv
from .v1_receive import V1Receiver, V1Record


@dataclass(frozen=True, slots=True)
class AvReceiveBatch:
    acknowledgements: tuple[bytes, ...] = field(default=(), repr=False)
    records: tuple[V1Record, ...] = field(default=(), repr=False)


class AvReceiver:
    """One route, cookie, reliable receive state and parser for the entire session.

Initial activation requires a matching ACCEPT followed by a complete V1 encoding
header. Protocol changes and unexpected controls are terminal, not silently skipped.
No payload queue persists; the caller consumes each returned batch immediately.
"""

    def __init__(self, peer: tuple[str, int], conv: int, call_id: int, cookie: bytes, *,
                 clock: Callable[[], float] = time.monotonic) -> None:
        if type(call_id) is not int or not 0 <= call_id <= 0xFFFFFFFF:
            raise ValueError("invalid AV call identifier")
        if not isinstance(cookie, bytes) or len(cookie) != 8:
            raise ValueError("invalid AV cookie")
        self._call_id = call_id
        self._cookie = cookie
        self._transport = ReceiveLifecycle(peer, conv, clock=clock)
        self._parser = V1Receiver()
        self._accepted = False
        self._encoding: V1EncodingHeader | None = None

    @property
    def phase(self) -> str:
        return self._transport.phase

    @property
    def buffered_bytes(self) -> int:
        return self._transport.buffered_bytes + self._parser.buffered_bytes

    def close(self) -> None:
        self._transport.close()
        self._parser.close()
        self._cookie = b""
        self._call_id = 0
        self._accepted = False
        self._encoding = None

    def poll(self) -> None:
        try:
            self._transport.poll()
        except ReceiveError:
            self.close()
            raise

    def _message(self, message: bytes) -> tuple[V1Record, ...]:
        if len(message) < 4 or struct.unpack_from("<H", message, 2)[0] != len(message):
            raise ValueError("invalid AV envelope")
        if message[0] == 3:
            if (len(message) != 76 or message[1] != 0
                    or struct.unpack_from("<I", message, 4)[0] != self._call_id
                    or struct.unpack_from("<I", message, 8)[0] != 2):
                raise ValueError("unexpected AV control")
            self._accepted = True
            return ()
        if message[0] != 4 or message[1] not in (1, 2) or not self._accepted:
            raise ValueError("media without correlated acceptance")
        records = self._parser.feed(decrypt_media_tlv(message, self._cookie))
        for record in records:
            if record.encoding is not None:
                if self._encoding is not None and self._encoding != record.encoding:
                    raise ValueError("AV encoding changed")
                self._encoding = record.encoding
                if self.phase == "initializing":
                    self._transport.activate()
            elif self._encoding is None:
                raise ValueError("AV record before encoding header")
        return records

    def receive(self, wire: bytes, peer: tuple[str, int]) -> AvReceiveBatch:
        try:
            batch = self._transport.receive(wire, peer)
            records: list[V1Record] = []
            for message in batch.messages:
                records.extend(self._message(message))
            return AvReceiveBatch(batch.acknowledgements, tuple(records))
        except ValueError:
            # No payload/cookie in exceptions; a fatal parser failure also kills
            # acknowledged transport state. The owner must close its real socket.
            self.close()
            raise ReceiveError("AV receive session failed") from None
