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
    bounded_timeout = max(0.1, min(float(timeout), 2.0))
    acknowledged = 0
    sent = 0
    aborted = False
    started_at = time.monotonic()
    session_deadline = (
        started_at + len(frames) * FRAME_INTERVAL_SECONDS + MAX_TRANSPORT_SLACK_SECONDS
    )
    last_send_at: float | None = None

    for index, audio_frame in enumerate(frames):
        if time.monotonic() >= session_deadline:
            aborted = True
            break
        if last_send_at is not None:
            due = last_send_at + FRAME_INTERVAL_SECONDS
            remaining = due - time.monotonic()
            if remaining > 0:
                time.sleep(remaining)
        last_send_at = time.monotonic()
        packet = build_v1_audio_packet((audio_frame,), index * 20)
        media = encrypt_media_tlv(packet, cookie)
        wire = build_kcp_push(
            conv,
            sequence,
            media,
            unacknowledged=inbound_next.get(conv, 0),
        )
        frame_acknowledged = False
        for _attempt in range(3):
            now = time.monotonic()
            if now >= session_deadline:
                break
            sock.sendto(wire, peer)
            for response, source in receive_datagrams(
                sock, min(now + min(bounded_timeout, 0.4), session_deadline)
            ):
                if source != peer or response[:2] != b"\xc0\x10":
                    continue
                try:
                    segments = parse_kcp_segments(response)
                except ValueError:
                    continue
                for segment in segments:
                    if (
                        segment.command == KCP_ACK
                        and segment.conv == conv
                        and segment.sequence == sequence
                    ):
                        frame_acknowledged = True
                        continue
                    if segment.command != KCP_PUSH:
                        continue
                    inbound_next[segment.conv] = max(
                        inbound_next.get(segment.conv, 0), segment.sequence + 1
                    )
                    sock.sendto(
                        build_kcp_ack(
                            segment.conv,
                            segment.sequence,
                            segment.timestamp,
                            unacknowledged=inbound_next[segment.conv],
                        ),
                        peer,
                    )
                if frame_acknowledged:
                    break
            if frame_acknowledged:
                break
        sent += 1
        sequence += 1
        if not frame_acknowledged:
            aborted = True
            break
        acknowledged += 1

    return LegacyAudioSendResult(len(frames), sent, acknowledged, sequence, aborted)
