"""Proprietary Yoosee RTSP talkback recovered from the camera firmware.

The firmware advertises ``USER_CMD_SET`` alongside the standard RTSP methods.  Its handler opens
audio output with an ``AudioCtlCmd: OPEN`` header and consumes fixed-size PCM blocks inside RTP
packets on interleaved TCP channel 2.  The payload type remains 8 for compatibility with its RTSP
parser, but the proprietary speaker path bypasses the advertised G.711 capture codec: firmware
mixing code reads the 320-byte payload directly as signed 16-bit samples.  This module contains
that family-specific lifecycle; the generic RTSP layer only supplies request and interleaved-frame
primitives.
"""

from __future__ import annotations

import secrets
import struct
import time
from collections.abc import Callable, Iterable
from typing import TYPE_CHECKING

from ...audio_format import PCM_FRAME_BYTES, PCM_FRAME_MS
from ...discovery import rtsp
from ..contracts import AudioMessageResult

if TYPE_CHECKING:
    from ...db.registry import Camera

RTP_PAYLOAD_BYTES = 320  # 160 native 16 kHz s16le samples.
RTP_CHANNEL = 2
RTP_PAYLOAD_TYPE = 8
RTP_CLOCK_RATE = 16_000
RTP_TIMESTAMP_STEP = 160
RTP_PACKET_SECONDS = 0.010
PRELOAD_PACKETS = 10  # The live camera needs about 100 ms queued to avoid speaker underflow.
OPEN_REFRESH_SECONDS = 4.0  # Firmware closes its decoder after roughly five seconds.


class RtspBackchannelError(RuntimeError):
    """The camera rejected or interrupted its proprietary LAN talkback path."""


def supported(camera: Camera) -> bool:
    """Whether enough local material exists to attempt the firmware RTSP backchannel."""

    return bool(camera.last_ip and camera.stream_path and camera.rtsp_port)


def pack_rtp(payload: bytes, sequence: int, timestamp: int, ssrc: int, *, marker: bool) -> bytes:
    """Pack one proprietary PCM RTP record (the RTSP ``$`` framing is added later)."""

    if len(payload) != RTP_PAYLOAD_BYTES:
        raise ValueError("Yoosee RTSP talkback requires exactly 320 PCM payload bytes")
    payload_type = RTP_PAYLOAD_TYPE | (0x80 if marker else 0)
    return (
        struct.pack(
            "!BBHII",
            0x80,
            payload_type,
            sequence & 0xFFFF,
            timestamp & 0xFFFFFFFF,
            ssrc & 0xFFFFFFFF,
        )
        + payload
    )


class RtspTalkSession:
    """Own the RTSP probe/media sockets and proprietary speaker lifecycle."""

    def __init__(
        self,
        camera: Camera,
        *,
        session_factory: Callable[..., rtsp.RtspSession] = rtsp.RtspSession,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if not supported(camera):
            raise RtspBackchannelError("camera has no usable local RTSP address")
        self._camera = camera
        self._uri = f"rtsp://{camera.last_ip}:{camera.rtsp_port}{camera.stream_path}"
        self._session_factory = session_factory
        self._session = self._new_session()
        self._clock = clock
        self._challenge: bytes | None = None
        self._opened = False
        self._refresh_at = 0.0
        self._sequence = secrets.randbelow(0x10000)
        self._timestamp = secrets.randbelow(0x100000000)
        self._ssrc = secrets.randbelow(0xFFFFFFFF) + 1
        self._first_packet = True

    def _new_session(self) -> rtsp.RtspSession:
        return self._session_factory(self._camera.last_ip, self._camera.rtsp_port, 4.0)

    def _authorization(self, method: str) -> str | None:
        if self._challenge is None:
            return None
        return rtsp.auth_header(
            self._challenge,
            method,
            self._uri,
            self._camera.username,
            self._camera.password,
        )

    def _request(
        self,
        method: str,
        *,
        accept_sdp: bool = False,
        extra_headers: dict[str, str] | None = None,
    ) -> bytes:
        response = self._session.request(
            method,
            self._uri,
            auth=self._authorization(method),
            accept_sdp=accept_sdp,
            extra_headers=extra_headers,
        )
        if response is None:
            raise RtspBackchannelError(f"camera did not answer RTSP {method}")
        return response

    def _control(self, value: str) -> None:
        response = self._request(
            "USER_CMD_SET",
            extra_headers={"Content-type": "", "AudioCtlCmd": value},
        )
        if rtsp.parse_status(response) != 200:
            raise RtspBackchannelError(
                f"camera rejected RTSP audio {value.lower()} ({rtsp.parse_status(response)})"
            )

    def open(self) -> None:
        """Verify credentials/capability and enable the camera's audio decoder."""

        response = self._session.request("DESCRIBE", self._uri, accept_sdp=True)
        if response is None:
            raise RtspBackchannelError("camera did not answer RTSP DESCRIBE")
        if rtsp.parse_status(response) == 401:
            if not self._camera.username:
                raise RtspBackchannelError("camera requires RTSP credentials")
            self._challenge = response
            response = self._request("DESCRIBE", accept_sdp=True)
        if rtsp.parse_status(response) != 200:
            raise RtspBackchannelError(
                f"camera rejected RTSP credentials ({rtsp.parse_status(response)})"
            )
        options = self._request("OPTIONS")
        public = rtsp.parse_headers(options).get("public", "").upper()
        if rtsp.parse_status(options) != 200 or "USER_CMD_SET" not in public:
            raise RtspBackchannelError("camera does not advertise Yoosee RTSP talkback")

        # This firmware's USER_CMD_SET parser is stateful and fragile.  Keep capability/auth
        # verification off the media socket, then reproduce the native minimal talk connection.
        self._session.close()
        self._session = self._new_session()
        self._challenge = None
        self._control("OPEN")
        self._opened = True
        self._refresh_at = self._clock() + OPEN_REFRESH_SECONDS

    def send_pcm(self, payload: bytes) -> None:
        if not self._opened:
            raise RtspBackchannelError("RTSP talkback is not open")
        if self._clock() >= self._refresh_at:
            self._control("OPEN")
            self._refresh_at = self._clock() + OPEN_REFRESH_SECONDS
        packet = pack_rtp(
            payload,
            self._sequence,
            self._timestamp,
            self._ssrc,
            marker=self._first_packet,
        )
        self._session.send_interleaved(RTP_CHANNEL, packet)
        self._first_packet = False
        self._sequence = (self._sequence + 1) & 0xFFFF
        self._timestamp = (self._timestamp + RTP_TIMESTAMP_STEP) & 0xFFFFFFFF

    def close(self) -> bool:
        closed = not self._opened
        try:
            if self._opened:
                self._control("CLOSE")
                closed = True
        except (OSError, RtspBackchannelError):
            # If media left the parser out of sync, clear the camera-global decoder flag through
            # the same minimal control request on a fresh socket.  Firmware inspection and the
            # live proof both confirm USER_CMD_SET is connection-independent.
            recovery = None
            try:
                recovery = self._new_session()
                response = recovery.request(
                    "USER_CMD_SET",
                    self._uri,
                    extra_headers={"Content-type": "", "AudioCtlCmd": "CLOSE"},
                )
                closed = response is not None and rtsp.parse_status(response) == 200
            except OSError:
                closed = False
            finally:
                if recovery is not None:
                    recovery.close()
        finally:
            self._opened = False
            self._session.close()
        return closed


def _send_chunks(
    camera: Camera,
    chunks: Iterable[bytes],
    *,
    session_factory: Callable[..., rtsp.RtspSession] = rtsp.RtspSession,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> AudioMessageResult:
    session = RtspTalkSession(camera, session_factory=session_factory, clock=clock)
    requested_frames = 0
    sent_frames = 0
    opened = False
    released = False
    pending: list[bytes] = []
    playback_started: float | None = None
    sent_packets = 0

    def send_pending(*, preload: bool = False) -> None:
        nonlocal playback_started, sent_packets
        if playback_started is None:
            playback_started = clock()
        while pending:
            if not preload or sent_packets >= PRELOAD_PACKETS:
                deadline = playback_started + (
                    sent_packets - PRELOAD_PACKETS + 1
                ) * RTP_PACKET_SECONDS
                wait = deadline - clock()
                if wait > 0:
                    sleep(wait)
            session.send_pcm(pending.pop(0))
            sent_packets += 1

    try:
        session.open()
        opened = True
        for chunk in chunks:
            if len(chunk) != PCM_FRAME_BYTES:
                raise ValueError("RTSP talkback requires complete 20 ms PCM frames")
            requested_frames += 1
            pending.extend(
                chunk[offset : offset + RTP_PAYLOAD_BYTES]
                for offset in range(0, PCM_FRAME_BYTES, RTP_PAYLOAD_BYTES)
            )
            if playback_started is None and len(pending) < PRELOAD_PACKETS:
                continue
            send_pending(preload=playback_started is None)
            sent_frames = requested_frames
        if pending:
            send_pending(preload=True)
            sent_frames = requested_frames
        if playback_started is not None:
            # CLOSE disables the camera-global decoder immediately. Let its queued PCM drain first.
            wait = playback_started + sent_packets * RTP_PACKET_SECONDS - clock()
            if wait > 0:
                sleep(wait)
    finally:
        released = session.close()

    if requested_frames == 0:
        raise ValueError("RTSP talkback requires at least one PCM frame")
    return AudioMessageResult(
        duration_ms=requested_frames * PCM_FRAME_MS,
        requested_frames=requested_frames,
        sent_frames=sent_frames,
        acknowledged_frames=sent_frames,
        direct_connection=True,
        session_completed=opened and released,
        route_released=released,
    )


def send(camera: Camera, pcm16le: bytes) -> AudioMessageResult:
    if len(pcm16le) % PCM_FRAME_BYTES:
        raise ValueError("RTSP talkback requires complete 20 ms PCM frames")
    return _send_chunks(
        camera,
        (
            pcm16le[offset : offset + PCM_FRAME_BYTES]
            for offset in range(0, len(pcm16le), PCM_FRAME_BYTES)
        ),
    )


def send_stream(camera: Camera, chunks: Iterable[bytes]) -> AudioMessageResult:
    return _send_chunks(camera, chunks)
