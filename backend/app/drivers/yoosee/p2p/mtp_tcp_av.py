"""Exclusive, socket-free TCP measurement/AV lifecycle for bounded experiments.

The caller supplies a freshly paired relay socket and must send returned bytes
on that socket only, poll silence, consume records immediately and close on any
failure. This module neither connects nor releases the broker route.
"""

import struct
import time
from collections.abc import Callable
from typing import NoReturn

from .av_handshake import AvHandshake
from .av_receive import AvReceiveBatch
from .kcp_receive import ReceiveError
from .mtp_tcp_ack import build_mtp_tcp_meter_ack, build_mtp_tcp_plain_meter_ack
from .mtp_tcp_framing import MtpTcpFramer
from .mtp_tcp_kcp import unwrap_tcp_relay_kcp, wrap_tcp_relay_kcp
from .mtp_tcp_measurement import build_mtp_tcp_measurement, matches_mtp_tcp_measurement_ack

MAX_RX_BYTES = 2 * 1024 * 1024
MAX_TX_BYTES = 256 * 1024
MAX_RECORDS = 4096


class TcpAvSession:
    """One measured route, two AV conversations, no reconnect or state reset.

    Local policy: 3 seconds for one measurement, 15 seconds total including CLOSE,
    and bounded cumulative traffic. Measurement receipt does not imply AV ready.
    Returned wires count as attempted; a partial/failed send requires close().
    """

    def __init__(self, peer: tuple[str, int], link_id: int, call_id: int, cookie: bytes,
                 *, source_id: int, destination_id: int,
                 request_user_data: bytes | None = None,
                 clock: Callable[[], float] = time.monotonic) -> None:
        if type(link_id) is not int or not 0 < link_id <= 0xFFFFFF:
            raise ValueError("invalid fresh TCP AV link")
        if not isinstance(cookie, bytes) or len(cookie) != 8:
            raise ValueError("invalid TCP AV cookie")
        if request_user_data is not None and (
                not isinstance(request_user_data, bytes) or len(request_user_data) != 32):
            raise ValueError("invalid TCP AV startup metadata")
        self._clock, self._created = clock, clock()
        self._tick = int(self._created * 1000)
        self._measurement = build_mtp_tcp_measurement(
            relay_link_id=link_id, source_id=source_id, destination_id=destination_id,
            call_id=call_id, sequence=1, timestamp_ms=self._tick, session_role=1,
        )
        self._peer, self._link, self._call = peer, link_id, call_id
        self._source, self._destination = source_id, destination_id
        self._cookie, self._user_data = cookie, request_user_data
        self._framer = MtpTcpFramer()
        self._av: AvHandshake | None = None
        self._measurement_sent = False
        self.closed = False
        self.received_bytes = self.sent_bytes = self.received_records = 0

    @property
    def metered(self) -> bool:
        return not self.closed and self._av is not None

    @property
    def ready(self) -> bool:
        return not self.closed and self._av is not None and self._av.ready

    @property
    def close_acknowledged(self) -> bool:
        return not self.closed and self._av is not None and self._av.close_acknowledged

    @property
    def buffered_bytes(self) -> int:
        return self._framer.buffered_bytes + (self._av.buffered_bytes if self._av else 0)

    def close(self) -> None:
        self.closed = True
        self._framer.abort()
        if self._av is not None:
            self._av.close()
        self._measurement = self._cookie = b""
        self._user_data = None

    def _fail(self, reason: str) -> NoReturn:
        self.close()
        raise ReceiveError(reason)

    def poll(self) -> None:
        if self.closed:
            raise ReceiveError("TCP AV session is closed")
        if self._clock() - self._created >= (15 if self.metered else 3):
            self._fail("TCP AV session deadline exceeded")
        try:
            if self._av is not None:
                self._av.poll()
        except ReceiveError:
            self.close()
            raise

    def _account(self, wires: tuple[bytes, ...]) -> tuple[bytes, ...]:
        self.sent_bytes += sum(map(len, wires))
        if self.sent_bytes > MAX_TX_BYTES:
            self._fail("TCP AV transmit budget exceeded")
        return wires

    def _wrap(self, wire: bytes) -> bytes:
        conv = struct.unpack_from("<I", wire, 6)[0]
        if conv not in (self._link, self._link | 0x80000000):
            raise ValueError("unexpected outbound AV conversation")
        return wrap_tcp_relay_kcp(wire, destination_id=self._destination,
                                  expected_conversation=conv)

    def due(self) -> tuple[bytes, ...]:
        self.poll()
        try:
            if self._av is not None:
                return self._account(tuple(self._wrap(wire) for wire in self._av.due()))
            if self._measurement_sent:
                return ()
            self._measurement_sent = True
            return self._account((self._measurement,))
        except (ValueError, ReceiveError):
            self._fail("TCP AV outgoing control failed")

    def _meter(self, wire: bytes) -> bytes | None:
        expected = dict(expected_link_id=self._link, expected_source_id=self._destination,
                        expected_destination_id=self._source)
        if (self._av is None and self._measurement_sent
                and matches_mtp_tcp_measurement_ack(wire, **expected,
                    expected_sequence=1, expected_timestamp_ms=self._tick)):
            self._av = AvHandshake(self._peer, self._link, self._call, self._cookie,
                                    request_user_data=self._user_data, clock=self._clock)
            self._measurement = b""
            return None
        offset = 14 if wire[1] == 0xD0 else 6
        if len(wire) > offset + 1 and wire[offset + 1] == 1:
            builder = build_mtp_tcp_meter_ack if offset == 14 else build_mtp_tcp_plain_meter_ack
            return builder(wire, **expected)
        return None  # Unmatched/duplicate ACKs never extend a deadline.

    def receive(self, data: bytes, peer: tuple[str, int]) -> AvReceiveBatch:
        self.poll()
        if peer != self._peer:
            self._fail("TCP AV connection peer changed")
        if not isinstance(data, bytes):
            self._fail("TCP AV input must be bytes")
        try:
            self.received_bytes += len(data)
            if self.received_bytes > MAX_RX_BYTES:
                self._fail("TCP AV receive budget exceeded")
            acknowledgements = []
            records = []
            unhandled = 0
            for wire in self._framer.feed(data):
                self.received_records += 1
                if self.received_records > MAX_RECORDS:
                    self._fail("TCP AV record budget exceeded")
                if wire[1] & 0x80:
                    reply = self._meter(wire)
                    if reply is not None:
                        acknowledgements.append(reply)
                    continue
                if self._av is None:
                    self._fail("TCP AV data before measurement receipt")
                offset = 14 if wire[1] == 0x50 else 6
                conv = struct.unpack_from("<I", wire, offset)[0]
                if conv not in (self._link, self._link | 0x80000000):
                    self._fail("TCP AV record belongs to another session")
                canonical = unwrap_tcp_relay_kcp(wire, expected_conversation=conv)
                batch = self._av.receive(canonical, self._peer)
                acknowledgements.extend(self._wrap(ack) for ack in batch.acknowledgements)
                records.extend(batch.records)
                unhandled += batch.unhandled_commands
            return AvReceiveBatch(self._account(tuple(acknowledgements)), tuple(records), unhandled)
        except (ValueError, ReceiveError):
            self._fail("TCP AV incoming stream failed")

    def begin_finish(self) -> None:
        self.poll()
        if self._av is None:
            self._fail("TCP AV CLOSE requires negotiated media")
        try:
            self._av.begin_finish()
        except ReceiveError:
            self.close()
            raise

    def finish(self) -> None:
        """Socket EOF always terminates locally, even with an incomplete record."""
        try:
            self._framer.finish()
        finally:
            self.close()
