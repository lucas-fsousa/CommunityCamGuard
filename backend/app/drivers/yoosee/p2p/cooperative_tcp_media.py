"""Finite TCP/AV pump driven by the account reader, never reading its UDP socket."""

import socket
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

from .contracts import P2PProbeError
from .kcp_receive import ReceiveError
from .media_policy import MediaPolicy, validate_media_policy
from .media_route_job import MediaRouteJob, MediaRouteSpec
from .mtp_tcp_av import TcpAvSession
from .mtp_tcp_handshake import build_mtp_tcp_pair_request
from .mtp_tcp_receive import PairedSocket
from .video_delivery import VideoDelivery


class OwnedTcpSocket(PairedSocket, Protocol):
    def close(self) -> None: ...


@dataclass(frozen=True, slots=True)
class TcpMediaResult:
    ready: bool
    close_acknowledged: bool
    socket_close_failed: bool
    video_frames: int
    audio_frames: int
    encoding_headers: int


class CooperativeTcpMedia:
    """One bounded receive per step; no broker callbacks, threads or frame history.

    This retains the finite measurement window, not a continuous video source.
    An optional bounded video handoff permits one consumer without blocking on it.
    """

    def __init__(self, sock: OwnedTcpSocket, peer: tuple[str, int], owner: TcpAvSession, *,
                 clock: Callable[[], float] = time.monotonic,
                 video: VideoDelivery | None = None):
        self._delivery = video
        self._sock, self._peer, self._owner, self._clock = sock, peer, owner, clock
        self._deadline = clock() + owner.profile.budget.receive_seconds
        self._step_until = self._deadline
        self._ready_at: float | None = None
        self._closing = False
        self._video = self._audio = self._headers = 0
        self.result: TcpMediaResult | None = None

    def _close(self) -> TcpMediaResult:
        if self.result is not None:
            return self.result
        confirmed = self._owner.close_acknowledged
        if self._delivery is not None:
            self._delivery.close()
        self._owner.close()
        failed = False
        try:
            self._sock.close()
        except OSError:
            failed = True
        self.result = TcpMediaResult(self._ready_at is not None, confirmed, failed,
                                      self._video, self._audio, self._headers)
        return self.result

    def _send(self, wire: bytes) -> None:
        remaining = min(self._deadline, self._step_until) - self._clock()
        if remaining <= 0:
            raise ReceiveError("cooperative TCP output deadline exceeded")
        self._sock.settimeout(min(0.02, remaining))
        self._sock.sendall(wire)

    def step(self) -> None:
        if self.result is not None:
            return
        try:
            self._step_until = self._clock() + 0.2
            if self._clock() >= self._deadline:
                raise ReceiveError("cooperative TCP media deadline exceeded")
            if self._sock.getpeername() != self._peer:
                raise ReceiveError("cooperative TCP peer mismatch")
            self._owner.poll()
            if (not self._closing and self._ready_at is not None and self._video
                    and self._owner.policy is MediaPolicy.FINITE
                    and self._clock() - self._ready_at >= self._owner.profile.budget.observation_seconds):
                self._begin_finish()
            for wire in self._owner.due():
                self._send(wire)
            remaining = min(self._deadline, self._step_until) - self._clock()
            if remaining <= 0:
                raise ReceiveError("cooperative TCP step deadline exceeded")
            self._sock.settimeout(min(0.001, remaining))
            try:
                data = self._sock.recv(4096)
            except TimeoutError:
                return
            if not data:
                raise ReceiveError("TCP media EOF before CLOSE receipt")
            if self._clock() >= min(self._deadline, self._step_until):
                raise ReceiveError("TCP media read exceeded deadline")
            batch = self._owner.receive(data, self._peer)
            if self._owner.ready and self._ready_at is None:
                self._ready_at = self._clock()
                if self._owner.policy is MediaPolicy.CONTINUOUS:
                    self._deadline = float("inf")  # Handshake/idle/assembly deadlines remain active.
            for wire in batch.acknowledgements:
                self._send(wire)
            for record in batch.records:
                if self._delivery is not None:
                    self._delivery.consume(record)
                self._video += bool(record.video)
                self._audio += len(record.audio)
                self._headers += record.encoding is not None
            if self._owner.close_acknowledged:
                self._close()
        except BaseException:
            self._close()
            raise

    def _begin_finish(self) -> None:
        if not self._closing:
            self._owner.begin_finish()
            self._closing = True
            self._deadline = min(self._deadline, self._clock() + 2)

    def finish(self) -> TcpMediaResult:
        """Bounded AVCLOSE drain before physical TCP close; idempotent, no reopen."""
        if self.result is not None:
            return self.result
        try:
            self._begin_finish()
            while self.result is None and self._clock() < self._deadline:
                self.step()
        except (OSError, ValueError, ReceiveError):
            pass
        finally:
            self._close()
        assert self.result is not None
        return self.result


def open_tcp_media(job: MediaRouteJob, spec: MediaRouteSpec, *,
                   clock: Callable[[], float] = time.monotonic,
                   policy: MediaPolicy = MediaPolicy.FINITE,
                   video: VideoDelivery | None = None) -> CooperativeTcpMedia:
    """One reviewed endpoint, bounded connect/pair, no broker login or retry.

    Trusted worker owns the broker reservation. Failed opening closes its TCP
    socket; unconfirmed physical close raises beyond ordinary transport failure.
    """
    validate_media_policy(policy)
    if job.endpoint is None or job.result is not None:
        raise ValueError("media route has no live correlated endpoint")
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    owner = None
    try:
        sock.settimeout(0.5)
        sock.connect(job.endpoint)
        sock.settimeout(0.2)
        sock.sendall(build_mtp_tcp_pair_request(relay_link_id=job.attempt.link_id,
            source_id=spec.access_id, destination_id=spec.device.device_id,
            timestamp_ms=int(clock() * 1000)))
        owner = TcpAvSession(job.endpoint, job.attempt.link_id, job.attempt.call_id,
            job.attempt.cookie, source_id=spec.access_id, destination_id=spec.device.device_id,
            request_user_data=job.metadata, clock=clock, policy=policy)
        return CooperativeTcpMedia(sock, job.endpoint, owner, clock=clock, video=video)
    except BaseException:
        if video is not None:
            video.close()
        if owner is not None:
            owner.close()
        try:
            sock.close()
        except OSError:
            raise P2PProbeError("TCP media opening cleanup unconfirmed") from None
        raise
