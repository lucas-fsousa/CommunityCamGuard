"""IoTVideo intercom control lifecycle with incremental AAC transport."""

from __future__ import annotations

import socket
import time
from collections.abc import Sequence

from .av_session import AvSessionResult
from .contracts import CallingResult
from .intercom_session import IntercomControlResult
from .media_protocol import (
    KCP_ACK,
    KCP_PUSH,
    build_av_control,
    build_kcp_ack,
    build_kcp_push,
    parse_kcp_segments,
)
from .modern_audio_sender import MAX_AUDIO_FRAMES, ModernAudioSender
from .session_io import receive_datagrams
from .stream_protocol import (
    MICROPHONE_STATE_CHANGE,
    decrypt_command_tlv,
    decrypt_media_tlv,
    encrypt_command_tlv,
    encrypt_media_tlv,
    pack_microphone_command,
    pack_v1_audio_encoding_header,
    parse_builtin_command,
    unpack_v1_sequence_user_data,
    unpack_v1_user_data_frames,
)

APPLICATION_RESPONSE_TIMEOUT_SECONDS = 10.0


class ModernIntercomSession:
    """Own START/header/AAC/STOP/CLOSE for an IoTVideo ``LivePlayer``."""

    def __init__(
        self,
        sock: socket.socket,
        calling: CallingResult,
        av: AvSessionResult,
        timeout: float,
        *,
        max_audio_frames: int = MAX_AUDIO_FRAMES,
    ) -> None:
        self._sock = sock
        self._attempt = calling.attempt
        self._peer = calling.peer_endpoint
        header = av.encoding_header
        self._valid = bool(
            self._attempt is not None
            and self._peer is not None
            and av.accepted
            and av.stream_version == 1
            and header is not None
            and (
                header.audio_codec,
                header.audio_codec_option,
                header.audio_channels,
                header.audio_bit_width,
                header.audio_sample_rate,
                header.audio_frame_size,
            )
            == (4, 2, 1, 16, 16_000, 1024)
        )
        link_id = self._attempt.link_id if self._attempt is not None else 0
        self._media_conv = link_id
        self._command_conv = link_id | 0x80000000
        self._media_sequence = 0
        self._command_sequence = av.next_send_sequence
        self._inbound_next = dict(av.inbound_next)
        self._header = header
        self._bounded_timeout = max(0.1, min(float(timeout), 5.0))
        self._max_audio_frames = max_audio_frames
        self._audio_sender: ModernAudioSender | None = None
        self._start_called = False
        self._closed = False
        self._av_start_acknowledged = False
        self._header_acknowledged = False
        self._talk_start_acknowledged = False
        self._talk_stop_acknowledged = False
        self._av_close_acknowledged = False

    def _matching_command_response(
        self,
        body: bytes,
        command: int,
        timestamp: int,
    ) -> bool:
        attempt = self._attempt
        if attempt is None or not body:
            return False
        try:
            if body[0] == 2:
                payload = decrypt_command_tlv(body, attempt.cookie)
            elif body[0] == 4:
                payload = decrypt_media_tlv(body, attempt.cookie)
            else:
                return False
            try:
                command_bodies: tuple[bytes, ...] = (unpack_v1_sequence_user_data(payload),)
            except ValueError:
                command_bodies = unpack_v1_user_data_frames(payload)
        except ValueError:
            return False
        for command_body in command_bodies:
            try:
                parsed = parse_builtin_command(command_body)
            except ValueError:
                continue
            if parsed.command == command and parsed.timestamp == timestamp:
                return True
        return False

    def _send_and_wait(
        self,
        conv: int,
        sequence: int,
        body: bytes,
        attempts: int,
        *,
        response_match: tuple[int, int] | None = None,
    ) -> bool:
        peer = self._peer
        if peer is None:
            return False
        wire = build_kcp_push(
            conv,
            sequence,
            body,
            unacknowledged=self._inbound_next.get(conv, 0),
        )
        acknowledged = False
        application_responded = response_match is None
        for _attempt in range(attempts):
            self._sock.sendto(wire, peer)
            receive_window = (
                APPLICATION_RESPONSE_TIMEOUT_SECONDS
                if response_match is not None
                else min(self._bounded_timeout, 0.4)
            )
            for response, source in receive_datagrams(
                self._sock, time.monotonic() + receive_window
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
                    if response_match is not None and self._matching_command_response(
                        segment.body, *response_match
                    ):
                        application_responded = True
                if acknowledged and application_responded:
                    break
            if acknowledged and application_responded:
                break
        return acknowledged and application_responded

    def start(self) -> bool:
        if self._closed:
            raise RuntimeError("modern intercom session is closed")
        if self._start_called:
            raise RuntimeError("modern intercom session has already started")
        self._start_called = True
        if not self._valid:
            return False
        attempt = self._attempt
        peer = self._peer
        header = self._header
        assert attempt is not None and peer is not None and header is not None

        self._av_start_acknowledged = self._send_and_wait(
            self._media_conv,
            self._media_sequence,
            build_av_control(attempt.call_id, 6),
            2,
        )
        self._media_sequence += 1
        if self._av_start_acknowledged:
            timestamp = time.time_ns() // 1_000
            command = encrypt_command_tlv(
                pack_microphone_command(True, timestamp_us=timestamp), attempt.cookie
            )
            self._talk_start_acknowledged = self._send_and_wait(
                self._command_conv,
                self._command_sequence,
                command,
                1,
                response_match=(MICROPHONE_STATE_CHANGE, timestamp & 0xFFFFFFFF),
            )
            self._command_sequence += 1
        if self._talk_start_acknowledged:
            encoding = encrypt_media_tlv(pack_v1_audio_encoding_header(header), attempt.cookie)
            self._header_acknowledged = self._send_and_wait(
                self._media_conv, self._media_sequence, encoding, 3
            )
            self._media_sequence += 1
        if self._header_acknowledged:
            self._audio_sender = ModernAudioSender(
                self._sock,
                peer,
                self._media_conv,
                attempt.cookie,
                self._inbound_next,
                self._media_sequence,
                self._bounded_timeout,
                max_frames=self._max_audio_frames,
            )
        return self._header_acknowledged

    def send_audio_frame(self, audio_frame: bytes) -> bool:
        if self._closed:
            raise RuntimeError("modern intercom session is closed")
        if self._audio_sender is None:
            raise RuntimeError("modern intercom talk state is not active")
        acknowledged = self._audio_sender.send(audio_frame)
        self._media_sequence = self._audio_sender.result().next_sequence
        return acknowledged

    def result(self) -> IntercomControlResult:
        audio = None
        if self._audio_sender is not None:
            snapshot = self._audio_sender.result()
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
        if self._closed:
            return self.result()
        self._closed = True
        if not self._valid or not self._start_called:
            return self.result()
        attempt = self._attempt
        assert attempt is not None
        if self._audio_sender is not None:
            self._media_sequence = self._audio_sender.close().next_sequence
        try:
            command = encrypt_command_tlv(pack_microphone_command(False), attempt.cookie)
            self._talk_stop_acknowledged = self._send_and_wait(
                self._command_conv, self._command_sequence, command, 4
            )
        finally:
            close = build_av_control(attempt.call_id, 7)
            self._av_close_acknowledged = self._send_and_wait(
                self._media_conv, self._media_sequence, close, 3
            )
        return self.result()


def run_modern_intercom_control(
    sock: socket.socket,
    calling: CallingResult,
    av: AvSessionResult,
    timeout: float,
    *,
    audio_frames: Sequence[bytes] = (),
) -> IntercomControlResult:
    """Run a bounded IoTVideo talk lifecycle with unconditional cleanup."""

    frame_bound = len(audio_frames) if audio_frames else MAX_AUDIO_FRAMES
    session = ModernIntercomSession(
        sock,
        calling,
        av,
        timeout,
        max_audio_frames=frame_bound,
    )
    try:
        if session.start():
            for audio_frame in audio_frames:
                if not session.send_audio_frame(audio_frame):
                    break
    finally:
        result = session.close()
    return result
