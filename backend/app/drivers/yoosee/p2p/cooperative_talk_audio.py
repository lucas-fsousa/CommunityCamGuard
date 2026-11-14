"""SDK-cadenced audio, with a small reliable window rather than stop-and-wait."""

from collections.abc import Callable

from .media_protocol import KCP_ACK, parse_kcp_segments
from .reliable_kcp_send import ReliableKcpPush
from .stream_protocol import build_v1_audio_packet, encrypt_media_tlv

MAX_INFLIGHT = 8  # At most640ms/32 AMR frames awaiting receipts, never unbounded.


class TalkAudioWindow:
    def __init__(self, peer: tuple[str, int], link: int, cookie: bytes,
                 frames: tuple[bytes, ...], allocate: Callable[[bool], int], *,
                 clock: Callable[[], float], wall_us: Callable[[], int]):
        self._peer, self._link, self._cookie = peer, link, cookie
        self._frames, self._allocate, self._clock, self._wall_us = frames, allocate, clock, wall_us
        self._pending: list[tuple[ReliableKcpPush, int]] = []
        self._offset = 0
        self._next_audio = clock()
        self._cancelled = False
        self.acknowledged_frames = 0

    @property
    def finished(self) -> bool:
        return (not self._pending and self._offset >= len(self._frames)
                and (self._cancelled or self._clock() >= self._next_audio))

    def cancel(self) -> None:
        self._cancelled = True
        self._frames = ()

    def close(self) -> None:
        self._frames, self._cookie = (), b""
        for request, _ in self._pending:
            request.close()
        self._pending.clear()

    def poll(self) -> None:
        for request, _ in self._pending:
            request.poll()

    def due(self) -> bytes | None:
        self.poll()
        now = self._clock()
        if (not self._cancelled and self._offset < len(self._frames)
                and len(self._pending) < MAX_INFLIGHT and now >= self._next_audio):
            frames = self._frames[self._offset:self._offset + 4]
            body = encrypt_media_tlv(build_v1_audio_packet(frames, self._wall_us()), self._cookie)
            request = ReliableKcpPush(self._peer, self._link, self._allocate(False), body, clock=self._clock)
            self._pending.append((request, len(frames)))
            self._offset += len(frames)
            self._next_audio = now + len(frames) * .02
            return request.due()
        for request, _ in self._pending:
            wire = request.due()
            if wire is not None:
                return wire
        return None

    def receive_ack(self, wire: bytes, peer: tuple[str, int]) -> None:
        if not self._pending or peer != self._peer:
            return
        try:
            if not any(s.command == KCP_ACK for s in parse_kcp_segments(wire)):
                return  # Don't parse every incoming video packet once per pending audio PUSH.
        except ValueError:
            return
        for request, count in tuple(self._pending):
            if request.receive_ack(wire, peer):
                self.acknowledged_frames += count
                request.close()
                self._pending.remove((request, count))
