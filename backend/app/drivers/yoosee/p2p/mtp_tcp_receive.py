"""Finite TCP AV receiver for an already paired, exclusively owned socket.

Internal experiment only: caller owns broker/camera reservations and physical
socket/route cleanup. No discovery, login, reconnect, decoder or production source.
"""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

from .av_sample import AvVideoSample
from .kcp_receive import ReceiveError
from .mtp_tcp_av import TcpAvSession


class PairedSocket(Protocol):
    def getpeername(self) -> tuple[str, int]: ...
    def settimeout(self, timeout: float) -> None: ...
    def sendall(self, wire: bytes) -> None: ...
    def recv(self, size: int) -> bytes: ...


@dataclass(frozen=True, slots=True)
class TcpReception:
    ready: bool
    close_acknowledged: bool
    video_frames: int
    audio_frames: int
    encoding_headers: int
    received_bytes: int
    sent_bytes: int


def receive_paired_av(sock: PairedSocket, peer: tuple[str, int], owner: TcpAvSession,
                      *, clock: Callable[[], float], sample: AvVideoSample | None = None,
                      service_broker: Callable[[], None] = lambda: None,
                      cancelled: Callable[[], bool] = lambda: False) -> TcpReception:
    """Observe three seconds after readiness, within eight seconds including CLOSE.

    Consume records immediately; never buffer complete messages outside the optional
    bounded sample. Return only after CLOSE receipt. Any failure terminates the
    owner and clears the supplied sample; never retry an ambiguous socket write.
    Caller must still close the actual TCP socket and request broker release.
    """
    deadline = clock() + 8
    ready_at = None
    closing = False
    video = audio = headers = 0
    try:
        if sock.getpeername() != peer:
            raise ReceiveError("TCP receive peer differs from paired route")

        def send(wire: bytes) -> None:
            if cancelled() or clock() >= deadline:
                raise ReceiveError("TCP reception cancelled or expired")
            sock.settimeout(min(0.2, max(0.001, deadline - clock())))
            sock.sendall(wire)

        while clock() < deadline:
            if cancelled():
                raise ReceiveError("TCP reception cancelled")
            service_broker()  # Must be a bounded, nonblocking same-socket service.
            if cancelled() or clock() >= deadline:
                raise ReceiveError("TCP reception cancelled or expired during broker service")
            owner.poll()
            if (not closing and owner.ready and ready_at is not None
                    and clock() - ready_at >= 3 and video):
                owner.begin_finish()
                closing = True
                deadline = min(deadline, clock() + 2)
            for wire in owner.due():
                send(wire)
            sock.settimeout(min(0.05, max(0.001, deadline - clock())))
            try:
                data = sock.recv(4096)
            except TimeoutError:
                continue
            if cancelled() or clock() >= deadline:
                raise ReceiveError("TCP reception cancelled or expired during socket read")
            if not data:
                raise ReceiveError("TCP reception ended before CLOSE receipt")
            batch = owner.receive(data, peer)
            if owner.ready and ready_at is None:
                ready_at = clock()
            for wire in batch.acknowledgements:
                send(wire)
            for record in batch.records:
                if sample is not None:
                    sample.consume(record)
                video += bool(record.video)
                audio += len(record.audio)
                headers += record.encoding is not None
            if owner.close_acknowledged:
                return TcpReception(ready_at is not None, True, video, audio, headers,
                                    owner.received_bytes, owner.sent_bytes)
        raise ReceiveError("TCP reception deadline exceeded")
    except BaseException:
        if sample is not None:
            sample.close()
        raise
    finally:
        owner.close()
