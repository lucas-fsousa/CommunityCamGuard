"""Bounded legacy intercom control lifecycle and incremental audio transport."""

from __future__ import annotations

import socket
import time
from collections.abc import Sequence
from dataclasses import dataclass

from .audio_sender import (
    MAX_AUDIO_FRAMES,
    LegacyAudioSender,
    LegacyAudioSendResult,
    validate_legacy_audio_frames,
)
from .av_session import AvSessionResult
from .contracts import CallingResult
from .media_protocol import (
    KCP_ACK,
    KCP_PUSH,
    build_av_control,
    build_kcp_ack,
    build_kcp_push,
    parse_kcp_segments,
)
from .session_io import receive_datagrams
from .stream_protocol import (
    encrypt_media_tlv,
    pack_legacy_capture_header,
    pack_legacy_talk_control,
)


@dataclass(frozen=True, slots=True)
class IntercomControlResult:
    av_start_acknowledged: bool
    header_acknowledged: bool
    talk_start_acknowledged: bool
    talk_stop_acknowledged: bool
    av_close_acknowledged: bool
    audio: LegacyAudioSendResult | None = None

    @property
    def completed(self) -> bool:
        return all(
            (
                self.av_start_acknowledged,
                self.header_acknowledged,
                self.talk_start_acknowledged,
                self.talk_stop_acknowledged,
                self.av_close_acknowledged,
                self.audio is None or self.audio.completed,
            )
        )


class LegacyIntercomSession:
    """Own one START/header/talk/audio/STOP/CLOSE control lifecycle."""

    def __init__(
        self,
        sock: socket.socket,
        calling: CallingResult,
        av: AvSessionResult,
        timeout: float,
        *,
        max_audio_frames: int = MAX_AUDIO_FRAMES,
        expected_audio_frames: int | None = None,
    ) -> None:
        self._sock = sock
        self._attempt = calling.attempt
        self._peer = calling.peer_endpoint
        self._valid = bool(
            self._attempt is not None
            and self._peer is not None
            and av.accepted
            and av.stream_version == 1
        )
        self._conv = self._attempt.link_id if self._attempt is not None else 0
        self._inbound_next = dict(av.inbound_next)
        self._sequence = 0
        self._bounded_timeout = max(0.1, min(float(timeout), 5.0))
        self._max_audio_frames = max_audio_frames
        self._expected_audio_frames = expected_audio_frames
        self._audio_sender: LegacyAudioSender | None = None
        self._start_called = False
        self._closed = False
        self._av_start_acknowledged = False
        self._header_acknowledged = False
        self._talk_start_acknowledged = False
        self._talk_stop_acknowledged = False
        self._av_close_acknowledged = False

    def _frame(self, body: bytes) -> bytes:
        return build_kcp_push(
            self._conv,
            self._sequence,
            body,
            unacknowledged=self._inbound_next.get(self._conv, 0),
        )

    def _send_and_wait(self, payload: bytes, attempts: int) -> bool:
        peer = self._peer
        if peer is None:
            return False
        acknowledged = False
        for _attempt in range(attempts):
            self._sock.sendto(payload, peer)
            for wire, source in receive_datagrams(
                self._sock, time.monotonic() + min(self._bounded_timeout, 0.4)
            ):
                if source != peer or wire[:2] != b"\xc0\x10":
                    continue
                try:
                    segments = parse_kcp_segments(wire)
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
                        peer,
                    )
                if acknowledged:
                    break
            if acknowledged:
                break
        return acknowledged

    def start(self) -> bool:
        """Open the legacy talk state and prepare the incremental audio sender."""

        if self._closed:
            raise RuntimeError("legacy intercom session is closed")
        if self._start_called:
            raise RuntimeError("legacy intercom session has already started")
        self._start_called = True
        if not self._valid:
            return False
        attempt = self._attempt
        peer = self._peer
        assert attempt is not None and peer is not None

        self._av_start_acknowledged = self._send_and_wait(
            self._frame(build_av_control(attempt.call_id, 6)), 2
        )
        self._sequence += 1
        if self._av_start_acknowledged:
            header = encrypt_media_tlv(pack_legacy_capture_header(), attempt.cookie)
            self._header_acknowledged = self._send_and_wait(self._frame(header), 3)
            self._sequence += 1
        if self._header_acknowledged:
            talk_on = encrypt_media_tlv(pack_legacy_talk_control(True), attempt.cookie)
            self._talk_start_acknowledged = self._send_and_wait(self._frame(talk_on), 3)
            self._sequence += 1
        if self._talk_start_acknowledged:
            self._audio_sender = LegacyAudioSender(
                self._sock,
                peer,
                self._conv,
                attempt.cookie,
                self._inbound_next,
                self._sequence,
                self._bounded_timeout,
                max_frames=self._max_audio_frames,
            )
        return self._talk_start_acknowledged

    def send_audio_frame(self, audio_frame: bytes) -> bool:
        """Send one frame only after the complete talk preamble was acknowledged."""

        if self._closed:
            raise RuntimeError("legacy intercom session is closed")
        if self._audio_sender is None:
            raise RuntimeError("legacy intercom talk state is not active")
        acknowledged = self._audio_sender.send(audio_frame)
        self._sequence = self._audio_sender.result().next_sequence
        return acknowledged

    def result(self) -> IntercomControlResult:
        audio: LegacyAudioSendResult | None = None
        if self._audio_sender is not None:
            snapshot = self._audio_sender.result(
                expected_frames=self._expected_audio_frames
            )
            if snapshot.requested_frames:
                audio = snapshot
        return IntercomControlResult(
            self._av_start_acknowledged,
            self._header_acknowledged,
            self._talk_start_acknowledged,
            self._talk_stop_acknowledged,
            self._av_close_acknowledged,
            audio,
        )

    def close(self) -> IntercomControlResult:
        """Unconditionally attempt talk STOP and AV CLOSE exactly once."""

        if self._closed:
            return self.result()
        self._closed = True
        if not self._valid or not self._start_called:
            return self.result()
        attempt = self._attempt
        assert attempt is not None
        if self._audio_sender is not None:
            self._sequence = self._audio_sender.close().next_sequence
        try:
            stop = encrypt_media_tlv(pack_legacy_talk_control(False), attempt.cookie)
            self._talk_stop_acknowledged = self._send_and_wait(self._frame(stop), 4)
        finally:
            self._sequence += 1
            close = build_av_control(attempt.call_id, 7)
            self._av_close_acknowledged = self._send_and_wait(self._frame(close), 3)
        return self.result()


def run_legacy_intercom_control(
    sock: socket.socket,
    calling: CallingResult,
    av: AvSessionResult,
    timeout: float,
    *,
    audio_frames: Sequence[bytes] = (),
) -> IntercomControlResult:
    """Exercise legacy talk with bounded optional audio and unconditional cleanup."""

    if audio_frames:
        validate_legacy_audio_frames(audio_frames)

    frame_bound = len(audio_frames) if audio_frames else MAX_AUDIO_FRAMES
    session = LegacyIntercomSession(
        sock,
        calling,
        av,
        timeout,
        max_audio_frames=frame_bound,
        expected_audio_frames=len(audio_frames) or None,
    )
    try:
        if session.start():
            for audio_frame in audio_frames:
                if not session.send_audio_frame(audio_frame):
                    break
    finally:
        result = session.close()
    return result


def run_silent_legacy_intercom_control(
    sock: socket.socket,
    calling: CallingResult,
    av: AvSessionResult,
    timeout: float,
) -> IntercomControlResult:
    """Validate START/STOP/CLOSE while making audio transmission impossible."""

    return run_legacy_intercom_control(sock, calling, av, timeout)
