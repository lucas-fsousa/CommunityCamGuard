"""Bounded, paced AMR-NB transport for an established legacy talk session."""

from __future__ import annotations

import socket
import time
from collections.abc import Sequence
from dataclasses import dataclass

from .media_protocol import (
    KCP_ACK,
    KCP_PUSH,
    build_kcp_ack,
    build_kcp_push,
    parse_kcp_segments,
)
from .session_io import receive_datagrams
from .stream_protocol import build_v1_audio_packet, encrypt_media_tlv

AMR_MODE_7_TOC = 0x3C
AMR_MODE_7_FRAME_BYTES = 32
FRAME_INTERVAL_SECONDS = 0.020
MAX_AUDIO_FRAMES = 500  # Ten seconds at one AMR frame per 20 ms.
MAX_TRANSPORT_SLACK_SECONDS = 2.0


@dataclass(frozen=True, slots=True)
class LegacyAudioSendResult:
    requested_frames: int
    sent_frames: int
    acknowledged_frames: int
    next_sequence: int
    aborted: bool

    @property
    def completed(self) -> bool:
        return (
            self.requested_frames > 0
            and self.sent_frames == self.requested_frames
            and self.acknowledged_frames == self.requested_frames
            and not self.aborted
        )


def validate_legacy_audio_frames(frames: Sequence[bytes]) -> None:
    """Reject unbounded input and anything other than raw AMR-NB mode-7 frames."""

    if not frames:
        raise ValueError("at least one AMR-NB frame is required")
    if len(frames) > MAX_AUDIO_FRAMES:
        raise ValueError("legacy talk audio exceeds the ten-second safety bound")
    if any(len(frame) != AMR_MODE_7_FRAME_BYTES or frame[0] != AMR_MODE_7_TOC for frame in frames):
        raise ValueError("legacy talk requires complete AMR-NB mode-7 frames")


def _validate_legacy_audio_frame(frame: bytes) -> None:
    if len(frame) != AMR_MODE_7_FRAME_BYTES or frame[0] != AMR_MODE_7_TOC:
        raise ValueError("legacy talk requires a complete AMR-NB mode-7 frame")


class LegacyAudioSender:
    """Incrementally send a strictly bounded legacy talk utterance.

    The sender deliberately waits for each KCP ACK before accepting the next
    frame. Pacing is based on the previous actual send time, so a delayed client
    cannot create a catch-up burst toward the camera.
    """

    def __init__(
        self,
        sock: socket.socket,
        peer: tuple[str, int],
        conv: int,
        cookie: bytes,
        inbound_next: dict[int, int],
        sequence: int,
        timeout: float,
        *,
        max_frames: int = MAX_AUDIO_FRAMES,
    ) -> None:
        if not 1 <= max_frames <= MAX_AUDIO_FRAMES:
            raise ValueError("legacy talk frame bound must be between one and 500")

        self._sock = sock
        self._peer = peer
        self._conv = conv
        self._cookie = cookie
        self._inbound_next = inbound_next
        self._sequence = sequence
        self._bounded_timeout = max(0.1, min(float(timeout), 2.0))
        self._max_frames = max_frames
        self._session_deadline = (
            time.monotonic()
            + max_frames * FRAME_INTERVAL_SECONDS
            + MAX_TRANSPORT_SLACK_SECONDS
        )
        self._last_send_at: float | None = None
        self._requested_frames = 0
        self._sent_frames = 0
        self._acknowledged_frames = 0
        self._aborted = False
        self._closed = False

    def send(self, audio_frame: bytes) -> bool:
        """Send one AMR frame, returning whether the camera acknowledged it."""

        if self._closed:
            raise RuntimeError("legacy talk sender is closed")
        if self._aborted:
            raise RuntimeError("legacy talk sender has aborted")
        if self._requested_frames >= self._max_frames:
            raise ValueError("legacy talk audio exceeds the configured safety bound")
        _validate_legacy_audio_frame(audio_frame)

        if time.monotonic() >= self._session_deadline:
            self._aborted = True
            return False
        if self._last_send_at is not None:
            due = self._last_send_at + FRAME_INTERVAL_SECONDS
            remaining = due - time.monotonic()
            if remaining > 0:
                time.sleep(remaining)

        timestamp_ms = self._requested_frames * 20
        self._requested_frames += 1
        self._last_send_at = time.monotonic()
        packet = build_v1_audio_packet((audio_frame,), timestamp_ms)
        media = encrypt_media_tlv(packet, self._cookie)
        wire = build_kcp_push(
            self._conv,
            self._sequence,
            media,
            unacknowledged=self._inbound_next.get(self._conv, 0),
        )
        frame_acknowledged = False
        for _attempt in range(3):
            now = time.monotonic()
            if now >= self._session_deadline:
                break
            self._sock.sendto(wire, self._peer)
            for response, source in receive_datagrams(
                self._sock,
                min(now + min(self._bounded_timeout, 0.4), self._session_deadline),
            ):
                if source != self._peer or response[:2] != b"\xc0\x10":
                    continue
                try:
                    segments = parse_kcp_segments(response)
                except ValueError:
                    continue
                for segment in segments:
                    if (
                        segment.command == KCP_ACK
                        and segment.conv == self._conv
                        and segment.sequence == self._sequence
                    ):
                        frame_acknowledged = True
                        continue
                    if segment.command != KCP_PUSH:
                        continue
                    self._inbound_next[segment.conv] = max(
                        self._inbound_next.get(segment.conv, 0), segment.sequence + 1
                    )
                    self._sock.sendto(
                        build_kcp_ack(
                            segment.conv,
                            segment.sequence,
                            segment.timestamp,
                            unacknowledged=self._inbound_next[segment.conv],
                        ),
                        self._peer,
                    )
                if frame_acknowledged:
                    break
            if frame_acknowledged:
                break

        self._sent_frames += 1
        self._sequence += 1
        if not frame_acknowledged:
            self._aborted = True
            return False
        self._acknowledged_frames += 1
        return True

    def result(self, *, expected_frames: int | None = None) -> LegacyAudioSendResult:
        """Return a snapshot without closing the incremental sender."""

        requested_frames = (
            self._requested_frames if expected_frames is None else expected_frames
        )
        return LegacyAudioSendResult(
            requested_frames,
            self._sent_frames,
            self._acknowledged_frames,
            self._sequence,
            self._aborted,
        )

    def close(self) -> LegacyAudioSendResult:
        """Prevent any additional frames and return the final counters."""

        self._closed = True
        return self.result()


def send_legacy_audio_frames(
    sock: socket.socket,
    peer: tuple[str, int],
    conv: int,
    cookie: bytes,
    inbound_next: dict[int, int],
    sequence: int,
    frames: Sequence[bytes],
    timeout: float,
) -> LegacyAudioSendResult:
    """Send one frame per packet, awaiting KCP ACK before advancing the queue.

    A missing ACK aborts the remaining utterance instead of feeding an unbounded
    retransmit/backlog burst into the camera. Camera PUSH traffic observed while
    waiting is acknowledged so the reverse stream cannot deadlock this channel.
    """

    validate_legacy_audio_frames(frames)
    sender = LegacyAudioSender(
        sock,
        peer,
        conv,
        cookie,
        inbound_next,
        sequence,
        timeout,
        max_frames=len(frames),
    )
    for audio_frame in frames:
        if not sender.send(audio_frame):
            break
    result = sender.result(expected_frames=len(frames))
    sender.close()
    return result
