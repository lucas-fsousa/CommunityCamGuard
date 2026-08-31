"""Device-scoped incremental PCM intercom orchestration for Yoosee cameras."""

from __future__ import annotations

import math
import socket
import time
from collections.abc import Iterable

from ....db.p2p import P2PEnrollment
from .aac_lc import AAC_FRAME_INTERVAL_SECONDS, AacLcAdtsEncoder
from .amr_nb import AmrNbEncoder
from .av_session import initialize_av_session
from .camera_session import open_camera_session
from .contracts import CallingResult, CertifiedNode, OnlineDevice, P2PProbeError
from .intercom_result import IntercomProbeResult, empty_intercom_result
from .intercom_session import LegacyIntercomSession
from .media_session import open_media_channel
from .modern_intercom_session import ModernIntercomSession
from .player_family import PlayerFamily, player_family
from .rendezvous_session import call_device, close_device_route
from .renewal import run_with_fresh_access

MAX_STREAM_SECONDS = 10.0


class PcmIntercomStream:
    """Own one direct route and accept bounded PCM chunks until explicit close."""

    def __init__(
        self,
        enrollment: P2PEnrollment,
        *,
        timeout: float = 1.5,
        total_timeout: float = 45.0,
        max_seconds: float = MAX_STREAM_SECONDS,
    ) -> None:
        if not 0.1 <= max_seconds <= MAX_STREAM_SECONDS:
            raise ValueError("P2P intercom stream limit must be between 0.1 and 10 seconds")
        self._enrollment = enrollment
        self._timeout = max(0.5, min(float(timeout), 5.0))
        self._deadline = time.monotonic() + max(8.0, min(float(total_timeout), 45.0))
        self._family = player_family(enrollment.device_id)
        self._max_frames = (
            math.ceil(max_seconds / AAC_FRAME_INTERVAL_SECONDS) + 3
            if self._family is PlayerFamily.IOTVIDEO
            else int(max_seconds * 50)
        )
        self._max_seconds = max_seconds
        self._sock: socket.socket | None = None
        self._node: CertifiedNode | None = None
        self._target: OnlineDevice | None = None
        self._calling: CallingResult | None = None
        self._control: LegacyIntercomSession | ModernIntercomSession | None = None
        self._encoder: AmrNbEncoder | AacLcAdtsEncoder | None = None
        self._direct_handshake = False
        self._media_meter_acknowledged = False
        self._av_accepted = False
        self._stream_version: int | None = None
        self._route_released = False
        self._started = False
        self._closed = False

    def _remaining_timeout(self) -> float:
        return min(self._timeout, max(0.1, self._deadline - time.monotonic()))

    def start(self) -> bool:
        if self._closed:
            raise RuntimeError("P2P intercom stream is closed")
        if self._started:
            raise RuntimeError("P2P intercom stream has already started")
        self._started = True
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.bind(("", 0))
        self._sock = sock

        node, target, _sequence = open_camera_session(
            sock,
            self._enrollment,
            self._timeout,
            self._deadline,
        )
        self._node = node
        self._target = target
        calling = call_device(
            sock,
            node,
            self._enrollment.access_id,
            target,
            self._remaining_timeout(),
            deadline=self._deadline,
        )
        self._calling = calling
        self._direct_handshake = calling.direct_handshake
        if not self._direct_handshake:
            return False
        media = open_media_channel(
            sock,
            node,
            self._enrollment.access_id,
            target,
            calling,
            self._remaining_timeout(),
        )
        self._media_meter_acknowledged = media.meter_acknowledged
        if not self._media_meter_acknowledged:
            return False
        av = initialize_av_session(sock, calling, self._remaining_timeout())
        self._av_accepted = av.accepted
        self._stream_version = av.stream_version
        if not av.accepted or av.stream_version != 1:
            return False

        control_type = (
            ModernIntercomSession
            if self._family is PlayerFamily.IOTVIDEO
            else LegacyIntercomSession
        )
        self._control = control_type(
            sock,
            calling,
            av,
            self._remaining_timeout(),
            max_audio_frames=self._max_frames,
        )
        if not self._control.start():
            return False
        self._encoder = (
            AacLcAdtsEncoder(max_seconds=self._max_seconds)
            if self._family is PlayerFamily.IOTVIDEO
            else AmrNbEncoder(max_seconds=self._max_seconds)
        )
        return True

    def feed_pcm16le(self, pcm16le: bytes) -> int:
        """Encode and acknowledge every complete frame emitted by one PCM chunk."""

        if self._closed:
            raise RuntimeError("P2P intercom stream is closed")
        if self._encoder is None or self._control is None:
            raise RuntimeError("P2P intercom stream is not active")
        if time.monotonic() >= self._deadline:
            raise P2PProbeError("P2P intercom stream exceeded its absolute deadline")
        frames = self._encoder.feed(pcm16le)
        for frame in frames:
            if not self._control.send_audio_frame(frame):
                raise P2PProbeError("camera stopped acknowledging intercom audio")
        return len(frames)

    def _result(self) -> IntercomProbeResult:
        control = (
            self._control.result()
            if self._control is not None
            else empty_intercom_result(self._enrollment.device_id).control
        )
        return IntercomProbeResult(
            self._enrollment.device_id,
            self._direct_handshake,
            self._media_meter_acknowledged,
            self._av_accepted,
            self._stream_version,
            control,
            self._route_released,
        )

    def close(self, *, flush: bool = False) -> IntercomProbeResult:
        """Close codec/control/route/socket once; optionally send one padded final frame."""

        if self._closed:
            return self._result()
        self._closed = True
        try:
            if self._encoder is not None:
                if flush and self._control is not None:
                    for frame in self._encoder.feed(b"", final=True):
                        if not self._control.send_audio_frame(frame):
                            break
                self._encoder.close()
        finally:
            try:
                if self._control is not None:
                    self._control.close()
            finally:
                try:
                    calling = self._calling
                    if (
                        self._sock is not None
                        and self._node is not None
                        and self._target is not None
                        and calling is not None
                        and calling.route_link_id
                    ):
                        self._route_released = close_device_route(
                            self._sock,
                            self._node,
                            self._enrollment.access_id,
                            self._target,
                            calling.route_link_id,
                            (calling.next_sequence + 1) & 0xFFFFFFFF,
                            min(1.0, max(0.0, self._deadline - time.monotonic())),
                        )
                finally:
                    if self._sock is not None:
                        self._sock.close()
        return self._result()


def send_pcm_intercom_chunks(
    enrollment: P2PEnrollment,
    chunks: Iterable[bytes],
    *,
    timeout: float = 1.5,
    total_timeout: float = 45.0,
    max_seconds: float = MAX_STREAM_SECONDS,
) -> IntercomProbeResult:
    """Consume a trusted bounded PCM iterator under one serialized device session."""

    def operation(current: P2PEnrollment) -> IntercomProbeResult:
        stream = PcmIntercomStream(
            current,
            timeout=timeout,
            total_timeout=total_timeout,
            max_seconds=max_seconds,
        )
        completed_input = False
        try:
            if not stream.start():
                return stream.close()
            received = False
            for chunk in chunks:
                if not chunk:
                    raise ValueError("P2P intercom PCM chunks cannot be empty")
                received = True
                stream.feed_pcm16le(chunk)
            if not received:
                raise ValueError("P2P intercom PCM stream is empty")
            completed_input = True
        except P2PProbeError:
            raise
        except (OSError, RuntimeError, ValueError) as exc:
            raise P2PProbeError("P2P streaming audio intercom operation failed") from exc
        finally:
            result = stream.close(flush=completed_input)
        return result

    return run_with_fresh_access(enrollment, operation)
