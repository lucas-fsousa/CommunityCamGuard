"""Experimental bounded AV receive probe on an exclusively owned, fresh route.

No discovery, decoder, retained media, intercom, production registration or retry.
Caller must reserve the exact camera and open/meter a fresh route without consuming
any AV sequence space. Socket ownership transfers on entry, even on invalid input.
"""

from __future__ import annotations

import math
import socket
import time
from collections.abc import Callable
from dataclasses import dataclass

from .av_handshake import AvHandshake
from .contracts import CallingResult
from .kcp_receive import ReceiveError
from .media_session import MediaChannelResult

MAX_DATAGRAMS = 10_000
MAX_RECEIVED_BYTES = 8 * 1024 * 1024
MAX_SENT_BYTES = 2 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class AvProbeResult:
    ready: bool
    datagrams: int
    received_bytes: int
    sent_bytes: int
    headers: int
    video_frames: int
    audio_frames: int
    ignored_datagrams: int
    peak_buffered_bytes: int


def probe_av_socket(
    sock: socket.socket,
    calling: CallingResult,
    channel: MediaChannelResult,
    *,
    duration: float = 10.0,
    cancelled: Callable[[], bool] = lambda: False,
    clock: Callable[[], float] = time.monotonic,
) -> AvProbeResult:
    """Consume/count records for <=10 seconds; always close socket and AV state.

    Result readiness is negotiation/header readiness, not decoder validation.
    Unsupported routing/meter traffic is ignored, not acknowledged speculatively.
    Closing the socket is local cleanup, not proof of a peer-side AV teardown.
    """
    handshake: AvHandshake | None = None
    try:
        if not math.isfinite(duration) or not 0 < duration <= 10:
            raise ValueError("invalid AV probe duration")
        attempt, peer = calling.attempt, calling.peer_endpoint
        if (attempt is None or peer is None or not calling.direct_handshake
                or not channel.direct_acknowledged or not channel.meter_acknowledged):
            raise ValueError("AV probe requires a fresh metered direct route")
        handshake = AvHandshake(peer, attempt.link_id, attempt.call_id, attempt.cookie, clock=clock)
        deadline = clock() + duration
        datagrams = received = sent = headers = video = audio = ignored = peak = 0

        def check_cancelled() -> None:
            if cancelled():
                raise ReceiveError("AV probe cancelled")

        def send(wire: bytes) -> None:
            nonlocal sent
            check_cancelled()
            if clock() >= deadline:
                raise ReceiveError("AV probe send deadline exceeded")
            if sent + len(wire) > MAX_SENT_BYTES:
                raise ReceiveError("AV probe transmit budget exceeded")
            sent += len(wire)  # No retry after an ambiguous or partial send.
            if sock.sendto(wire, peer) != len(wire):
                raise ReceiveError("AV probe incomplete datagram send")

        while clock() < deadline:
            check_cancelled()
            sock.settimeout(min(0.05, max(0.001, deadline - clock())))
            for wire in handshake.due():
                send(wire)
            try:
                wire, source = sock.recvfrom(2048)  # MTP maximum is 2047; detect truncation.
            except TimeoutError:
                continue  # due()/poll() still enforces all protocol deadlines.
            check_cancelled()
            if clock() >= deadline:
                break
            datagrams += 1
            received += len(wire)
            if datagrams > MAX_DATAGRAMS or received > MAX_RECEIVED_BYTES:
                raise ReceiveError("AV probe receive budget exceeded")
            if source != peer or len(wire) > 2047 or wire[:2] != b"\xc0\x10":
                ignored += 1
                continue
            batch = handshake.receive(wire, source)
            for acknowledgement in batch.acknowledgements:
                send(acknowledgement)
            peak = max(peak, handshake.buffered_bytes)
            for record in batch.records:
                headers += record.encoding is not None
                video += bool(record.video)
                audio += len(record.audio)
        check_cancelled()
        handshake.poll()
        if not handshake.ready or not video:
            raise ReceiveError("AV probe ended without negotiated video")
        return AvProbeResult(handshake.ready, datagrams, received, sent,
                             headers, video, audio, ignored, peak)
    except OSError:
        raise ReceiveError("AV probe socket failure") from None
    finally:
        try:
            if handshake is not None:
                handshake.close()
        finally:
            sock.close()
