"""Paced AAC transport for an established IoTVideo intercom session."""

from __future__ import annotations

import socket
import time

from .aac_lc import AAC_FRAME_INTERVAL_SECONDS, validate_aac_lc_adts_frame
from .audio_sender import AudioSendResult
from .media_protocol import KCP_ACK, KCP_PUSH, build_kcp_ack, build_kcp_push, parse_kcp_segments
from .session_io import receive_datagrams
from .stream_protocol import build_v1_audio_packet, encrypt_media_tlv

MAX_AUDIO_FRAMES = 160
MAX_TRANSPORT_SLACK_SECONDS = 2.0


class ModernAudioSender:
    """Send one MPEG-4 AAC/ADTS frame per acknowledged v1 media packet."""

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
            raise ValueError("modern talk frame bound must be between one and 160")
        self._sock = sock
        self._peer = peer
        self._conv = conv
        self._cookie = cookie
        self._inbound_next = inbound_next
        self._sequence = sequence
        self._bounded_timeout = max(0.1, min(float(timeout), 2.0))
        self._max_frames = max_frames
        self._session_deadline = (
            time.monotonic() + max_frames * AAC_FRAME_INTERVAL_SECONDS + MAX_TRANSPORT_SLACK_SECONDS
        )
        self._last_send_at: float | None = None
        self._requested_frames = 0
        self._sent_frames = 0
        self._acknowledged_frames = 0
        self._aborted = False
        self._closed = False

    def send(self, audio_frame: bytes) -> bool:
        if self._closed:
            raise RuntimeError("modern talk sender is closed")
        if self._aborted:
            raise RuntimeError("modern talk sender has aborted")
        if self._requested_frames >= self._max_frames:
            raise ValueError("modern talk audio exceeds the configured safety bound")
        validate_aac_lc_adts_frame(audio_frame)
        if time.monotonic() >= self._session_deadline:
            self._aborted = True
            return False
        if self._last_send_at is not None:
            remaining = self._last_send_at + AAC_FRAME_INTERVAL_SECONDS - time.monotonic()
            if remaining > 0:
                time.sleep(remaining)

        self._requested_frames += 1
        self._last_send_at = time.monotonic()
        packet = build_v1_audio_packet((audio_frame,), time.time_ns() // 1_000)
        media = encrypt_media_tlv(packet, self._cookie)
        wire = build_kcp_push(
            self._conv,
            self._sequence,
            media,
            unacknowledged=self._inbound_next.get(self._conv, 0),
        )
        acknowledged = False
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
                        acknowledged = True
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
                if acknowledged:
                    break
            if acknowledged:
                break

        self._sent_frames += 1
        self._sequence += 1
        if not acknowledged:
            self._aborted = True
            return False
        self._acknowledged_frames += 1
        return True

    def result(self, *, expected_frames: int | None = None) -> AudioSendResult:
        return AudioSendResult(
            self._requested_frames if expected_frames is None else expected_frames,
            self._sent_frames,
            self._acknowledged_frames,
            self._sequence,
            self._aborted,
        )

    def close(self) -> AudioSendResult:
        self._closed = True
        return self.result()
