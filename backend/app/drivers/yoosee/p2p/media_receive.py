"""Socket-free, pinned-peer MTP/KCP input adapter for native-video experiments.

Outputs ACK bytes and complete opaque messages, not decoded audio/video frames.
No route selection, INIT, microphone, reconnect, fan-out or production registration.
"""

from __future__ import annotations

from dataclasses import dataclass

from .kcp_receive import KcpReceiver
from .kcp_window import build_window_report
from .media_protocol import KCP_WASK, MTP_MAX_FRAME_SIZE, build_kcp_ack, parse_kcp_segments


@dataclass(frozen=True, slots=True)
class MediaReceiveBatch:
    acknowledgements: tuple[bytes, ...] = ()
    messages: tuple[bytes, ...] = ()


class MediaReceiver:
    """One negotiated peer/conversation and one bounded receiver, owned by caller.

    The owner sends returned ACKs only to ``peer``, promptly consumes messages and
    invokes ``receiver.expire()`` during silence. ReceiveError is terminal and must
    close the actual socket; this class never silently resets acknowledged state.
    """

    def __init__(self, peer: tuple[str, int], receiver: KcpReceiver) -> None:
        self.peer = peer
        self.receiver = receiver

    def receive(self, wire: bytes, peer: tuple[str, int]) -> MediaReceiveBatch:
        self.receiver.expire()
        if peer != self.peer or len(wire) > MTP_MAX_FRAME_SIZE:
            return MediaReceiveBatch()
        try:
            segments = parse_kcp_segments(wire)
        except ValueError:
            return MediaReceiveBatch()
        acknowledgements: list[bytes] = []
        messages: list[bytes] = []
        for segment in segments:
            if (segment.conv == self.receiver.conv and segment.command == KCP_WASK
                    and segment.fragment == 0 and not segment.body):
                # A peer that last saw window zero probes before sending again.
                # WINS reports only current credit/UNA; it acknowledges no PUSH
                # and must never advance sequence state or refresh a deadline.
                acknowledgements.append(build_window_report(segment.conv,
                    unacknowledged=self.receiver.next_sequence,
                    window=self.receiver.available_window))
                continue
            result = self.receiver.receive(segment)
            if result.acknowledge:
                acknowledgements.append(build_kcp_ack(
                    segment.conv, segment.sequence, segment.timestamp,
                    unacknowledged=result.unacknowledged,
                    window=self.receiver.available_window,
                ))
            messages.extend(result.messages)
        return MediaReceiveBatch(tuple(acknowledgements), tuple(messages))
