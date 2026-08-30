"""Bounded legacy intercom control lifecycle without audio transmission."""

from __future__ import annotations

import socket
import time
from collections.abc import Sequence
from dataclasses import dataclass

from .audio_sender import (
    LegacyAudioSendResult,
    send_legacy_audio_frames,
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

    attempt = calling.attempt
    peer = calling.peer_endpoint
    if attempt is None or peer is None or not av.accepted or av.stream_version != 1:
        return IntercomControlResult(False, False, False, False, False)
    conv = attempt.link_id
    inbound_next = dict(av.inbound_next)
    sequence = 0
    bounded_timeout = max(0.1, min(float(timeout), 5.0))

    def frame(body: bytes, current_sequence: int) -> bytes:
        return build_kcp_push(
            conv,
            current_sequence,
            body,
            unacknowledged=inbound_next.get(conv, 0),
        )

    def send_and_wait(payload: bytes, current_sequence: int, attempts: int) -> bool:
        acknowledged = False
        for _attempt in range(attempts):
            sock.sendto(payload, peer)
            for wire, source in receive_datagrams(
                sock, time.monotonic() + min(bounded_timeout, 0.4)
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
                        and segment.conv == conv
                        and segment.sequence == current_sequence
                    ):
                        acknowledged = True
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
                if acknowledged:
                    break
            if acknowledged:
                break
        return acknowledged

    av_start_acknowledged = False
    header_acknowledged = False
    talk_start_acknowledged = False
    talk_stop_acknowledged = False
    av_close_acknowledged = False
    audio: LegacyAudioSendResult | None = None
    try:
        av_start_acknowledged = send_and_wait(
            frame(build_av_control(attempt.call_id, 6), sequence), sequence, 2
        )
        sequence += 1
        if av_start_acknowledged:
            header = encrypt_media_tlv(pack_legacy_capture_header(), attempt.cookie)
            header_acknowledged = send_and_wait(frame(header, sequence), sequence, 3)
            sequence += 1
        if header_acknowledged:
            start = encrypt_media_tlv(pack_legacy_talk_control(True), attempt.cookie)
            talk_start_acknowledged = send_and_wait(frame(start, sequence), sequence, 3)
            sequence += 1
        if talk_start_acknowledged and audio_frames:
            audio = send_legacy_audio_frames(
                sock,
                peer,
                conv,
                attempt.cookie,
                inbound_next,
                sequence,
                audio_frames,
                bounded_timeout,
            )
            sequence = audio.next_sequence
    finally:
        try:
            stop = encrypt_media_tlv(pack_legacy_talk_control(False), attempt.cookie)
            talk_stop_acknowledged = send_and_wait(frame(stop, sequence), sequence, 4)
        finally:
            sequence += 1
            close = build_av_control(attempt.call_id, 7)
            av_close_acknowledged = send_and_wait(frame(close, sequence), sequence, 3)
    return IntercomControlResult(
        av_start_acknowledged,
        header_acknowledged,
        talk_start_acknowledged,
        talk_stop_acknowledged,
        av_close_acknowledged,
        audio,
    )


def run_silent_legacy_intercom_control(
    sock: socket.socket,
    calling: CallingResult,
    av: AvSessionResult,
    timeout: float,
) -> IntercomControlResult:
    """Validate START/STOP/CLOSE while making audio transmission impossible."""

    return run_legacy_intercom_control(sock, calling, av, timeout)
