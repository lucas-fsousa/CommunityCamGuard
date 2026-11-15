"""Bounded reviewed audio on the already active AV route.

Pinned IoTVideo LivePlayer TX: HEADER_ENC(0x0500), builtin microphone 0x32,
four mode-7 frames per burst (SDK threshold 64ms), microphone OFF. This is a
transport candidate, not a claim of speaker compatibility or acoustic success.
"""

import logging
import time
from collections.abc import Callable

from .cooperative_talk_audio import TalkAudioWindow
from .kcp_receive import ReceiveError
from .legacy_talk_input import LegacyTalkInput
from .microphone_reply import MicrophoneReply
from .negotiated_talk_input import AAC_TALK_FORMAT, NegotiatedAacTalkInput
from .reliable_kcp_send import ReliableKcpPush
from .stream_protocol import (
    V1EncodingHeader,
    encrypt_media_tlv,
    pack_legacy_capture_header,
    pack_legacy_talk_control,
    pack_microphone_command,
    pack_v1_audio_encoding_header,
)
from .talk_stream_input import StreamingTalkInput, TalkFrames, validate_talk_frames

# df.a.P / LivePlayer: independent of the camera's incoming AAC descriptor.
TALK_FORMAT = V1EncodingHeader(0x0102, 5, 0, 1, 16, 8000, 160, 0, 0, 0, 0)
log = logging.getLogger(__name__)


class CooperativeTalk:
    """Account-thread only, at most64KiB finite audio and eight in-flight PUSHes.

    No sleeps, receive loop, sockets, authentication, or AV START/CLOSE. Sequence
    allocation belongs to the parent handshake. Missing receipts invalidate that
    route: we cannot skip a KCP sequence or assert that microphone OFF executed.
    """

    def __init__(self, peer: tuple[str, int], link: int, cookie: bytes,
                 frames: TalkFrames, allocate: Callable[[bool], int], *,
                 clock: Callable[[], float] = time.monotonic,
                 wall_us: Callable[[], int] = lambda: time.time_ns() // 1000):
        if not isinstance(cookie, bytes) or len(cookie) != 8:
            raise ValueError("invalid talk cookie")
        validate_talk_frames(frames)
        self._legacy = isinstance(frames, LegacyTalkInput)
        negotiated_aac = isinstance(frames, NegotiatedAacTalkInput)
        self._format = AAC_TALK_FORMAT if negotiated_aac else TALK_FORMAT
        if isinstance(frames, (LegacyTalkInput, NegotiatedAacTalkInput)):
            frames = frames.frames
        self._stream = frames if isinstance(frames, StreamingTalkInput) else None
        if self._stream is not None:
            self._stream.claim()
        self._peer, self._link, self._cookie = peer, link, cookie
        self._frames, self._allocate, self._clock, self._wall_us = frames, allocate, clock, wall_us
        self._deadline = clock() + 16
        self._request: ReliableKcpPush | None = None
        self._audio = TalkAudioWindow(peer, link, cookie, frames, allocate, clock=clock, wall_us=wall_us,
                                     frame_seconds=.064 if negotiated_aac else .02,
                                     burst_frames=1 if negotiated_aac else 4)
        self._stage = "header"
        self._cancelled = False
        self.done = self.closed = False
        self.microphone_started = self.microphone_stopped = False
        self.acknowledged_frames = 0
        self._microphone_tokens: dict[int, str] = {}
        self._microphone_replies: set[int] = set()

    def receive_microphone_reply(self, reply: MicrophoneReply) -> None:
        stage = self._microphone_tokens.get(reply.timestamp)
        if stage is None or reply.timestamp in self._microphone_replies:
            return
        self._microphone_replies.add(reply.timestamp)
        log.warning('native_talk_microphone_reply stage=%s origin=%s flags=%s payload_size=%s result=%s',
                    stage, reply.origin, reply.flags, reply.payload_size, reply.result)

    @property
    def cancelled(self) -> bool:
        return self._cancelled

    def cancel(self) -> None:
        """Drain an already emitted PUSH, then send only microphone OFF."""
        self._cancelled = True
        self._frames = ()
        self._audio.cancel()

    def close(self) -> None:
        """Local retirement only; never invent remote microphone OFF evidence."""
        self.closed = True
        self._cookie, self._frames = b"", ()
        self._audio.close()
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
        self._audio.poll()
        if self._stream is not None and self._stream.aborted and not self.cancelled:
            self.cancel()

    def _complete(self) -> None:
        log.warning('native_talk_microphone_receipts commands=%s correlated_replies=%s',
                    len(self._microphone_tokens), len(self._microphone_replies))
        self.done = True
        self._cookie, self._frames = b"", ()
        self._audio.close()

    def _prepare(self) -> None:
        if self._stage == "header":
            if self._cancelled:
                self._complete()
                return
            header = (pack_legacy_capture_header() if self._legacy
                      else pack_v1_audio_encoding_header(self._format))
            body = encrypt_media_tlv(header, self._cookie)
        elif self._stage == "start":
            if self._cancelled:
                self._complete()
                return
            # StreamPipe v1: sequenced user data goes through send_data_link_chn
            # and the AV ring (base KCP/type4). Only v2 takes the separate command
            # channel here; see pinned StreamPipe::Impl::send_data at0x13714c.
            body = self._microphone_command(True)
        else:
            body = self._microphone_command(False)
        self._request = ReliableKcpPush(self._peer, self._link,
            self._allocate(False), body, clock=self._clock)

    def _microphone_command(self, enabled: bool) -> bytes:
        if self._legacy:
            return encrypt_media_tlv(pack_legacy_talk_control(enabled), self._cookie)
        timestamp = self._wall_us()
        self._microphone_tokens[timestamp & 0xFFFFFFFF] = 'start' if enabled else 'stop'
        return encrypt_media_tlv(pack_microphone_command(enabled, timestamp_us=timestamp), self._cookie)

    def due(self) -> bytes | None:
        self.poll()
        if self.done:
            return None
        if self._stage == "audio":
            if not self._audio.finished:
                return self._audio.due()
            self._stage = "stop"
        if self._request is None:
            self._prepare()
        return self._request.due() if self._request is not None else None

    def receive_ack(self, wire: bytes, peer: tuple[str, int]) -> None:
        self.poll()
        if self._stage == "audio":
            self._audio.receive_ack(wire, peer)
            self.acknowledged_frames = self._audio.acknowledged_frames
            return
        request = self._request
        if request is None or not request.receive_ack(wire, peer):
            return
        log.warning('native_talk_control_receipt stage=%s sequence=%s peer_next=%s',
                    self._stage, request.sequence, request.peer_next_sequence)
        request.close()
        self._request = None
        if self._stage == "header":
            self._stage = "start"
        elif self._stage == "start":
            self.microphone_started = True  # Transport receipt ONLY.
            self._stage = "audio"
        else:
            self.microphone_stopped = True  # Transport receipt ONLY.
            self._complete()
