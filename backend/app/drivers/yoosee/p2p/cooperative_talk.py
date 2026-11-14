"""Finite AMR message on an already active AV route; never opens/closes AV.

Pinned IoTVideo LivePlayer TX: HEADER_ENC(0x0500), builtin microphone 0x32,
four mode-7 frames per burst (SDK threshold 64ms), microphone OFF. This is a
transport candidate, not a claim of speaker compatibility or acoustic success.
"""

import time
from collections.abc import Callable

from .audio_sender import FRAME_INTERVAL_SECONDS, validate_amr_audio_frames
from .kcp_receive import ReceiveError
from .reliable_kcp_send import ReliableKcpPush
from .stream_protocol import (
    V1EncodingHeader,
    build_v1_audio_packet,
    encrypt_command_tlv,
    encrypt_media_tlv,
    pack_microphone_command,
    pack_v1_audio_encoding_header,
)

# df.a.P / LivePlayer: independent of the camera's incoming AAC descriptor.
TALK_FORMAT = V1EncodingHeader(0x0102, 5, 0, 1, 16, 8000, 160, 0, 0, 0, 0)


class CooperativeTalk:
    """Account-thread only, at most 16KiB of audio and one in-flight PUSH.

    No sleeps, receive loop, sockets, authentication, or AV START/CLOSE. Sequence
    allocation belongs to the parent handshake. Missing receipts invalidate that
    route: we cannot skip a KCP sequence or assert that microphone OFF executed.
    """

    def __init__(self, peer: tuple[str, int], link: int, cookie: bytes,
                 frames: tuple[bytes, ...], allocate: Callable[[bool], int], *,
                 clock: Callable[[], float] = time.monotonic,
                 wall_us: Callable[[], int] = lambda: time.time_ns() // 1000):
        if not isinstance(cookie, bytes) or len(cookie) != 8:
            raise ValueError("invalid talk cookie")
        if not isinstance(frames, tuple) or any(not isinstance(f, bytes) for f in frames):
            raise ValueError("talk requires immutable encoded frames")
        validate_amr_audio_frames(frames)
        self._peer, self._link, self._cookie = peer, link, cookie
        self._frames, self._allocate, self._clock, self._wall_us = frames, allocate, clock, wall_us
        self._deadline = clock() + 16
        self._request: ReliableKcpPush | None = None
        self._stage = "header"
        self._offset = self._batch_frames = 0
        self._next_audio = clock()
        self._cancelled = False
        self.done = self.closed = False
        self.microphone_started = self.microphone_stopped = False
        self.acknowledged_frames = 0

    @property
    def cancelled(self) -> bool:
        return self._cancelled

    def cancel(self) -> None:
        """Drain an already emitted PUSH, then send only microphone OFF."""
        self._cancelled = True
        self._frames = ()

    def close(self) -> None:
        """Local retirement only; never invent remote microphone OFF evidence."""
        self.closed = True
        self._cookie, self._frames = b"", ()
        if self._request is not None:
            self._request.close()
            self._request = None

    def poll(self) -> None:
        if self.closed:
            raise ReceiveError("native talk is closed")
        if self.done:
            return
        if self._clock() >= self._deadline:
            raise ReceiveError("native talk deadline exceeded")
        if self._request is not None:
            self._request.poll()

    def _complete(self) -> None:
        self.done = True
        self._cookie, self._frames = b"", ()

    def _prepare(self) -> None:
        high = False
        if self._stage == "header":
            if self._cancelled:
                self._complete()
                return
            body = encrypt_media_tlv(pack_v1_audio_encoding_header(TALK_FORMAT), self._cookie)
        elif self._stage == "start":
            if self._cancelled:
                self._complete()
                return
            high = True
            body = encrypt_command_tlv(pack_microphone_command(True,
                timestamp_us=self._wall_us()), self._cookie)
        elif self._stage == "audio":
            if self._cancelled or self._offset >= len(self._frames):
                self._stage = "stop"
                self._prepare()
                return
            frames = self._frames[self._offset:self._offset + 4]
            self._batch_frames = len(frames)
            body = encrypt_media_tlv(build_v1_audio_packet(frames, self._wall_us()), self._cookie)
            # Pace from actual emission, never catch up in a burst after a stall.
            self._next_audio = self._clock() + len(frames) * FRAME_INTERVAL_SECONDS
        else:
            high = True
            body = encrypt_command_tlv(pack_microphone_command(False,
                timestamp_us=self._wall_us()), self._cookie)
        self._request = ReliableKcpPush(self._peer,
            self._link | 0x80000000 if high else self._link,
            self._allocate(high), body, clock=self._clock)

    def due(self) -> bytes | None:
        self.poll()
        if self.done:
            return None
        if self._request is None:
            # Also let the final burst finish before microphone OFF.
            if self._stage == "audio" and not self._cancelled and self._clock() < self._next_audio:
                return None
            self._prepare()
        return self._request.due() if self._request is not None else None

    def receive_ack(self, wire: bytes, peer: tuple[str, int]) -> None:
        self.poll()
        request = self._request
        if request is None or not request.receive_ack(wire, peer):
            return
        request.close()
        self._request = None
        if self._stage == "header":
            self._stage = "start"
        elif self._stage == "start":
            self.microphone_started = True  # Transport receipt ONLY.
            self._stage = "audio"
        elif self._stage == "audio":
            self.acknowledged_frames += self._batch_frames
            self._offset += self._batch_frames
        else:
            self.microphone_stopped = True  # Transport receipt ONLY.
            self._complete()
