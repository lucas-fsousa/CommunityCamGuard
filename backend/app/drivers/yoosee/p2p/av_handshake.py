"""Socket-free AV INIT/ACCEPT/START ownership for experimental native reception.

No route discovery, socket I/O, authentication or production feature admission.
The caller must send returned wires promptly, consume all records and poll silence.
"""

from __future__ import annotations

import time
from collections.abc import Callable

from .av_control_send import ReliableAvControl
from .av_init_requests import AvInitRequests
from .av_receive import AvReceiveBatch, AvReceiver
from .cooperative_talk import CooperativeTalk
from .kcp_receive import ReceiveError
from .media_policy import MediaPolicy, validate_media_policy
from .negotiated_talk_input import NegotiatedAacTalkInput, matches_aac_talk
from .talk_stream_input import TalkFrames


class AvHandshake:
    """One freshly negotiated route; sender and parser objects never get replaced.

Readiness needs INIT receipt, correlated ACCEPT and peer START, local START receipt,
and parsed encoding. Media records can arrive before readiness: consume them with
the same parser/decoder owner, but do not claim ready or present a live source yet.
"""

    def __init__(self, peer: tuple[str, int], link_id: int, call_id: int, cookie: bytes, *,
                 request_user_data: bytes | None = None,
                 repeat_init: bool = False,
                 policy: MediaPolicy = MediaPolicy.FINITE,
                 clock: Callable[[], float] = time.monotonic) -> None:
        validate_media_policy(policy)
        self._clock, self._created = clock, clock()
        self._peer, self._link_id, self._call_id = peer, link_id, call_id
        self._init: ReliableAvControl | AvInitRequests
        self._init = (AvInitRequests(peer, link_id, call_id, clock=clock,
                                    request_user_data=request_user_data) if repeat_init
                      else ReliableAvControl(peer, link_id, call_id, action=1, clock=clock,
                                             request_user_data=request_user_data))
        self._receiver = AvReceiver(peer, link_id, call_id, cookie,
                                    control_conv=link_id | 0x80000000, clock=clock, policy=policy)
        self._start: ReliableAvControl | None = None
        self._finish: ReliableAvControl | None = None
        self._finish_requested = False
        self._next_media = 1  # START owns zero; audio and CLOSE share this space.
        self._next_control: int | None = None
        self._cookie = cookie
        self._talk: CooperativeTalk | None = None
        self.closed = False

    @property
    def ready(self) -> bool:
        return (not self.closed and not self._finish_requested and self._finish is None and self._init.acknowledged
                and self._receiver.accepted and self._receiver.peer_started
                and self._start is not None and self._start.acknowledged
                and self._receiver.phase == "active")

    def _allocate(self, high: bool) -> int:
        if high:
            if self._next_control is None:
                self._next_control = (self._init.next_sequence
                                      if isinstance(self._init, AvInitRequests) else 1)
            sequence = self._next_control
            self._next_control += 1
        else:
            sequence = self._next_media
            self._next_media += 1
        if sequence > 0xFFFFFFFF:
            raise ReceiveError("AV outgoing sequence exhausted")
        return sequence

    def begin_talk(self, frames: TalkFrames, *,
                   wall_us: Callable[[], int] = lambda: time.time_ns() // 1000) -> CooperativeTalk:
        """Owner-thread candidate only; no capability grant or dashboard routing.

        Caller must already have driver admission for this operation. The returned
        handle reports transport receipts, never speaker success. cancel() stops
        just this utterance; normal due/receive turns still own all incoming AV.
        """
        self.poll()
        if not self.ready or (self._talk is not None and not self._talk.done):
            raise ReceiveError("native talk requires an idle, active AV route")
        if isinstance(frames, NegotiatedAacTalkInput) and not matches_aac_talk(self._receiver.encoding):
            raise ValueError('native talk requires the matching negotiated AAC descriptor')
        candidate = CooperativeTalk(self._peer, self._link_id, self._cookie,
            frames, self._allocate, clock=self._clock, wall_us=wall_us)
        if self._talk is not None:
            self._talk.close()
        self._talk = candidate
        if isinstance(self._init, AvInitRequests) and not self._init.all_sequences_acknowledged:
            self._init.drain_pending()
        return self._talk

    @property
    def buffered_bytes(self) -> int:
        return self._receiver.buffered_bytes

    @property
    def close_acknowledged(self) -> bool:
        """Transport receipt only, not proof the peer released its media route."""
        return not self.closed and self._finish is not None and self._finish.acknowledged

    def begin_finish(self) -> None:
        """End a fully negotiated session; no other writer may use this route."""
        self.poll()
        if self._finish_requested:
            return
        if not self.ready:
            raise ReceiveError("AV CLOSE requires completed negotiation")
        self._finish_requested = True
        if self._talk is not None and not self._talk.done:
            self._talk.cancel()
        else:
            self._prepare_finish()

    def _prepare_finish(self) -> None:
        self._finish = ReliableAvControl(self._peer, self._link_id, self._call_id,
            action=7, sequence=self._allocate(False), _shared_close=True, clock=self._clock)

    def close(self) -> None:
        self.closed = True
        self._cookie = b""
        if self._talk is not None:
            self._talk.close()
        self._init.close()
        if self._start is not None:
            self._start.close()
        if self._finish is not None:
            self._finish.close()
        self._receiver.close()
        self._call_id = 0

    def poll(self) -> None:
        try:
            if self.closed:
                raise ReceiveError("AV handshake is closed")
            if not self._finish_requested and not self.ready and self._clock() - self._created >= 5:
                raise ReceiveError("AV handshake readiness deadline exceeded")
            if self._talk is not None and self._talk.cancelled and isinstance(self._init, AvInitRequests):
                self._init.suspend_pending()
            self._init.poll()
            if self._start is not None:
                self._start.poll()
            if self._finish is not None:
                self._finish.poll()
            if self._talk is not None:
                self._talk.poll()
            self._receiver.poll()
        except ReceiveError:
            self.close()
            raise

    def due(self) -> tuple[bytes, ...]:
        """At most one control per turn; SDK INIT repetition is UDP opt-in only."""
        self.poll()
        try:
            if self._finish is not None:
                wire = self._finish.due()
            elif self._talk is not None and not self._talk.done:
                if isinstance(self._init, AvInitRequests) and not self._init.all_sequences_acknowledged:
                    if self._talk.cancelled:
                        self._init.suspend_pending()
                        wire = self._talk.due()
                    else:
                        wire = self._init.due()
                else:
                    wire = self._talk.due()
            elif self._finish_requested:
                self._prepare_finish()
                assert self._finish is not None
                wire = self._finish.due()
            elif (not self._init.acknowledged or
                  (isinstance(self._init, AvInitRequests) and not self._receiver.accepted)):
                wire = self._init.due()
            elif self._receiver.accepted and (self._receiver.peer_started
                                              or isinstance(self._init, AvInitRequests)):
                if self._start is None:
                    self._start = ReliableAvControl(self._peer, self._link_id, self._call_id,
                                                    action=6, clock=self._clock)
                wire = self._start.due()
            else:
                wire = None
            return (wire,) if wire is not None else ()
        except ReceiveError:
            self.close()
            raise

    def receive(self, wire: bytes, peer: tuple[str, int]) -> AvReceiveBatch:
        self.poll()
        if not self._init.attempts:
            return AvReceiveBatch()  # Unsolicited traffic cannot start negotiation.
        try:
            self._init.receive_ack(wire, peer)
            if self._start is not None:
                self._start.receive_ack(wire, peer)
            if self._finish is not None:
                self._finish.receive_ack(wire, peer)
            if self._talk is not None and not self._talk.done:
                self._talk.receive_ack(wire, peer)
            batch = self._receiver.receive(wire, peer)
            if self._talk is not None:
                for reply in batch.microphone_replies:
                    self._talk.receive_microphone_reply(reply)
            if self._receiver.accepted and isinstance(self._init, AvInitRequests):
                self._init.stop_repeating()
            return batch
        except ReceiveError:
            self.close()
            raise
