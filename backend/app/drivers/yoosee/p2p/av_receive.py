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

from .av_pending import PendingAv
from .kcp_receive import KcpReceiver, ReceiveError
from .media_receive import MediaReceiver
from .receive_lifecycle import ReceiveLifecycle
from .stream_protocol import V1EncodingHeader, decrypt_media_tlv
from .v1_receive import V1Receiver, V1Record


@dataclass(frozen=True, slots=True)
class AvReceiveBatch:
    acknowledgements: tuple[bytes, ...] = field(default=(), repr=False)
    records: tuple[V1Record, ...] = field(default=(), repr=False)
    unhandled_commands: int = 0


class AvReceiver:
    """One route, cookie, reliable receive state and parser for the entire session.

    Single-channel mode requires ACCEPT and an encoding header. Paired mode requires
    ACCEPT on the high-bit conversation, then START and encoding on the base one.
    Sequence spaces remain independent. Early START/media wait in a small ciphertext
    queue until ACCEPT; the caller consumes returned batches immediately. Unknown
    commands are counted, never interpreted as acceptance or command success.
    """

    def __init__(self, peer: tuple[str, int], conv: int, call_id: int, cookie: bytes, *,
                 control_conv: int | None = None,
                 clock: Callable[[], float] = time.monotonic) -> None:
        if type(call_id) is not int or not 0 <= call_id <= 0xFFFFFFFF:
            raise ValueError("invalid AV call identifier")
        if not isinstance(cookie, bytes) or len(cookie) != 8:
            raise ValueError("invalid AV cookie")
        if control_conv is not None and (
                type(conv) is not int or not 0 < conv <= 0xFFFFFF
                or type(control_conv) is not int or control_conv != conv | 0x80000000):
            raise ValueError("invalid paired AV conversations")
        self._control = (MediaReceiver(peer, KcpReceiver(control_conv, clock=clock))
                         if control_conv is not None else None)
        self._call_id = call_id
        self._cookie = cookie
        self._transport = ReceiveLifecycle(peer, conv, clock=clock)
        self._parser = V1Receiver()
        self._pending = PendingAv(clock)
        self._accepted = False
        self._started = False
        self._encoding: V1EncodingHeader | None = None

    @property
    def phase(self) -> str:
        return self._transport.phase

    @property
    def accepted(self) -> bool:
        return self._accepted

    @property
    def peer_started(self) -> bool:
        return self._started

    @property
    def buffered_bytes(self) -> int:
        control_bytes = self._control.receiver.buffered_bytes if self._control is not None else 0
        return (self._transport.buffered_bytes + self._parser.buffered_bytes
                + control_bytes + self._pending.buffered_bytes)

    def close(self) -> None:
        self._transport.close()
        self._parser.close()
        self._pending.clear()
        if self._control is not None:
            self._control.receiver.close()
        self._cookie = b""
        self._call_id = 0
        self._accepted = False
        self._started = False
        self._encoding = None

    def poll(self) -> None:
        try:
            self._transport.poll()
            self._pending.poll()
            if self._control is not None:
                self._control.receiver.expire()
        except ReceiveError:
            self.close()
            raise

    def _check_control(self, message: bytes, action: int) -> None:
        if (len(message) != 76 or message[:4] != b"\x03\x00\x4c\x00"
                or struct.unpack_from("<I", message, 4)[0] != self._call_id
                or struct.unpack_from("<I", message, 8)[0] != action):
            raise ValueError("unexpected AV control")

    def _message(self, message: bytes) -> tuple[V1Record, ...]:
        if len(message) < 4 or struct.unpack_from("<H", message, 2)[0] != len(message):
            raise ValueError("invalid AV envelope")
        if self._control is not None and not self._accepted:
            if not self._pending.buffered_bytes or message[0] == 3:
                self._check_control(message, 6)
            elif message[0] != 4 or message[1] not in (1, 2):
                raise ValueError("unexpected pre-accept AV message")
            self._pending.append(message)
            return ()
        if message[0] == 3:
            self._check_control(message, 2 if self._control is None else 6)
            if self._control is None:
                self._accepted = True
            elif not self._accepted:
                raise ValueError("START before correlated ACCEPT")
            self._started = True
            return ()
        if message[0] != 4 or message[1] not in (1, 2) or not self._accepted or not self._started:
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
            self.poll()
            control_acks: tuple[bytes, ...] = ()
            unhandled = 0
            if self._control is not None:
                controls = self._control.receive(wire, peer)
                control_acks = controls.acknowledgements
                for message in controls.messages:
                    if (self._accepted and len(message) >= 4 and message[0] == 2
                            and message[1] in (1, 2)
                            and struct.unpack_from("<H", message, 2)[0] == len(message)):
                        # Command TLVs share the control conversation. Do not
                        # interpret them as ACCEPT or claim command execution.
                        unhandled += 1
                        continue
                    self._check_control(message, 2)
                    self._accepted = True
            batch = self._transport.receive(wire, peer)
            records: list[V1Record] = []
            if self._accepted:
                for message in self._pending.take():
                    records.extend(self._message(message))
            for message in batch.messages:
                records.extend(self._message(message))
            return AvReceiveBatch(control_acks + batch.acknowledgements, tuple(records), unhandled)
        except ValueError:
            # No payload/cookie in exceptions; a fatal parser failure also kills
            # acknowledged transport state. The owner must close its real socket.
            self.close()
            raise ReceiveError("AV receive session failed") from None
