"""Bounded cross-thread utterances, pinned to one account generation/media lease."""

import threading
from dataclasses import dataclass, field

from .talk_stream_input import StreamingTalkInput, TalkFrames, validate_talk_frames


@dataclass(frozen=True, slots=True)
class TalkTicket:
    generation: int
    serial: int


@dataclass(frozen=True, slots=True)
class TalkOutcome:
    status: str  # transport_received / cancelled / unavailable; never "played".
    acknowledged_frames: int = 0
    stop_acknowledged: bool = False


@dataclass(slots=True)
class _Utterance:
    lease: int
    frames: TalkFrames = field(repr=False)
    claimed: bool = False
    cancelled: bool = False
    abandoned: bool = False
    result: TalkOutcome | None = None


class TalkInbox:
    """Trusted MediaLeaseInbox admission only; no caller-provided device target.

    Slots include unread results, with at most one utterance per lease. Capacity
    comes from the authenticated cohort, not a fixed camera-count limit.
    """

    def __init__(self, generation: int, capacity: int):
        self._generation, self._capacity = generation, capacity
        self._lock = threading.Lock()
        self._serial = 0
        self._closed = False
        self._items: dict[TalkTicket, _Utterance] = {}

    def submit(self, lease: int, frames: TalkFrames) -> TalkTicket:
        validate_talk_frames(frames)
        with self._lock:
            if (self._closed or len(self._items) >= self._capacity or self._serial >= 1 << 64
                    or any(item.lease == lease for item in self._items.values())):
                raise ValueError("talk slot unavailable")
            if isinstance(frames, StreamingTalkInput):
                frames.bind()
            ticket = TalkTicket(self._generation, self._serial)
            self._serial += 1
            self._items[ticket] = _Utterance(lease, frames)
            return ticket

    def claim(self) -> tuple[TalkTicket, int, TalkFrames] | None:
        with self._lock:
            if self._closed:
                return None
            for ticket, item in self._items.items():
                if not item.claimed and item.result is None:
                    item.claimed = True
                    frames = item.frames
                    if not isinstance(frames, StreamingTalkInput):
                        item.frames = ()
                    return ticket, item.lease, frames
            return None

    def cancel(self, ticket: TalkTicket) -> None:
        with self._lock:
            item = self._items.get(ticket)
            if item is not None and item.result is None:
                item.cancelled = True
                if isinstance(item.frames, StreamingTalkInput):
                    item.frames.cancel()
                    if not item.claimed:
                        item.frames.close()
                else:
                    item.frames = ()
                if not item.claimed:
                    item.result = TalkOutcome("cancelled")

    def should_cancel(self, ticket: TalkTicket) -> bool:
        with self._lock:
            item = self._items.get(ticket)
            return self._closed or item is None or item.cancelled

    def abandon(self, ticket: TalkTicket) -> None:
        """Caller exited: discard queued work, or retain its slot until owner cleanup.

        A claimed utterance must still drain microphone OFF. Removing that slot
        early would allow another utterance to overlap it on the same camera.
        """
        with self._lock:
            item = self._items.get(ticket)
            if item is None:
                return
            if isinstance(item.frames, StreamingTalkInput):
                item.frames.cancel()
                if not item.claimed or item.result is not None:
                    item.frames.close()
            else:
                item.frames = ()
            if not item.claimed or item.result is not None:
                del self._items[ticket]
            else:
                item.cancelled = item.abandoned = True

    def finish(self, ticket: TalkTicket, result: TalkOutcome) -> None:
        with self._lock:
            item = self._items.get(ticket)
            if item is not None and item.claimed and item.result is None:
                if isinstance(item.frames, StreamingTalkInput):
                    item.frames.close()
                item.frames = ()
                if item.abandoned:
                    del self._items[ticket]
                else:
                    item.result = result

    def take(self, ticket: TalkTicket) -> TalkOutcome | None:
        with self._lock:
            item = self._items.get(ticket)
            if item is None or item.result is None:
                return None
            del self._items[ticket]
            return item.result

    def close(self) -> None:
        with self._lock:
            self._closed = True
            for item in self._items.values():
                if item.result is None:
                    item.cancelled = True
                    if isinstance(item.frames, StreamingTalkInput):
                        item.frames.cancel()
                        if not item.claimed:
                            item.frames.close()
                    else:
                        item.frames = ()
                    if not item.claimed:
                        item.result = TalkOutcome("cancelled")
