"""Experimental bounded AV receive probe on an exclusively owned, fresh route.

No discovery, decoder, retained media, intercom, production registration or retry.
Caller must reserve the exact camera and open/meter a fresh route without consuming
any AV sequence space. By default socket ownership transfers on entry, even on
invalid input. An enclosing route owner may retain it for B9 cleanup explicitly.
"""

from __future__ import annotations

import math
import socket
import time
from collections.abc import Callable
from dataclasses import dataclass

from .av_handshake import AvHandshake
from .av_meter import AvMeter
from .contracts import CallingResult
from .kcp_receive import ReceiveError
from .media_session import MediaChannelResult

MAX_DATAGRAMS = 10_000
MAX_RECEIVED_BYTES = 8 * 1024 * 1024
MAX_SENT_BYTES = 2 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class AvProbeResult:
    ready: bool
    close_acknowledged: bool
    datagrams: int
    received_bytes: int
    sent_bytes: int
    headers: int
    video_frames: int
    audio_frames: int
    ignored_datagrams: int
    peak_buffered_bytes: int
    meter_acknowledgements: int = 0


def probe_av_socket(
    sock: socket.socket,
    calling: CallingResult,
    channel: MediaChannelResult,
    *,
    duration: float = 10.0,
    cancelled: Callable[[], bool] = lambda: False,
    clock: Callable[[], float] = time.monotonic,
    close_socket: bool = True,
    meter: AvMeter | None = None,
) -> AvProbeResult:
    """Receive for <=10 seconds, plus <=2 seconds for CLOSE transport receipt.

    Result readiness is negotiation/header readiness, not decoder validation.
    Only a caller-supplied correlated meter responder handles maintenance requests.
    Closing the socket is local cleanup, not proof of a peer-side AV teardown.
    With close_socket=False, the enclosing owner must close it on every exit.
    """
    handshake: AvHandshake | None = None
    try:
        if not math.isfinite(duration) or not 0 < duration <= 10:
            raise ValueError("invalid AV probe duration")
        attempt, peer = calling.attempt, calling.peer_endpoint
        if (attempt is None or peer is None or not calling.direct_handshake
                or not channel.meter_roundtrip_confirmed):
            raise ValueError("AV probe requires a fresh metered direct route")
        handshake = AvHandshake(peer, attempt.link_id, attempt.call_id, attempt.cookie, clock=clock)
        deadline = clock() + duration
        datagrams = received = sent = headers = video = audio = ignored = peak = 0
        finishing = False

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

        while True:
            check_cancelled()
            if clock() >= deadline:
                if finishing:
                    raise ReceiveError("AV probe CLOSE receipt deadline exceeded")
                if not handshake.ready or not video:
                    raise ReceiveError("AV probe ended without negotiated video")
                handshake.begin_finish()
                finishing = True
                deadline = clock() + 2.0
            sock.settimeout(min(0.05, max(0.001, deadline - clock())))
            for wire in handshake.due():
                send(wire)
            try:
                wire, source = sock.recvfrom(2048)  # MTP maximum is 2047; detect truncation.
            except TimeoutError:
                continue  # due()/poll() still enforces all protocol deadlines.
            check_cancelled()
            if clock() >= deadline:
                continue
            datagrams += 1
            received += len(wire)
            if datagrams > MAX_DATAGRAMS or received > MAX_RECEIVED_BYTES:
                raise ReceiveError("AV probe receive budget exceeded")
            if meter is not None:
                response = meter.receive(wire, source)
                if response is not None:
                    send(response)
                    continue
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
            if handshake.close_acknowledged:
                break
        check_cancelled()
        handshake.poll()
        return AvProbeResult(True, handshake.close_acknowledged, datagrams, received, sent,
                             headers, video, audio, ignored, peak,
                             meter.acknowledgements if meter is not None else 0)
    except OSError:
        raise ReceiveError("AV probe socket failure") from None
    finally:
        try:
            if handshake is not None:
                handshake.close()
        finally:
            if close_socket:
                sock.close()
