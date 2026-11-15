"""Single-use bounded AMR producer/consumer handoff; never waits on the AV thread."""

import threading
import time
from collections import deque
from collections.abc import Callable

from .audio_sender import MAX_AUDIO_FRAMES, validate_amr_audio_frame, validate_amr_audio_frames
from .legacy_talk_input import LegacyTalkInput

MAX_QUEUED_FRAMES = 25  # 500ms,800 bytes; additional transport backlog has its own bound.
MAX_INPUT_SECONDS = 11  # Ten-second capture + startup margin; leave time for owned OFF drain.


class StreamingTalkInput:
    def __init__(self, *, clock: Callable[[], float] = time.monotonic):
        self._condition = threading.Condition()
        self._frames: deque[bytes] = deque()
        self._clock, self._last_input = clock, clock()
        self._created = self._last_input
        self._ended = self._closed = self._aborted = False
        self._bound = self._claimed = False
        self._received = 0

    @property
    def aborted(self) -> bool:
        with self._condition:
            return self._aborted

    @property
    def finished(self) -> bool:
        with self._condition:
            return self._ended and not self._frames

    def bind(self) -> None:
        with self._condition:
            if self._bound or self._claimed or self._closed or self._aborted:
                raise ValueError("talk input already used or unavailable")
            self._bound = True

    def claim(self) -> None:
        with self._condition:
            if self._claimed or self._closed or self._aborted:
                raise ValueError("talk input already consumed or unavailable")
            self._claimed = True

    def put(self, frame: bytes, *, timeout: float = 1.0) -> None:
        """Only the producer may wait; cancellation/retirement wakes it immediately."""
        if not isinstance(frame, bytes):
            raise ValueError("immutable AMR frame required")
        validate_amr_audio_frame(frame)
        if not 0 < timeout <= 2:
            raise ValueError("bounded talk producer timeout required")
        until = time.monotonic() + timeout
        with self._condition:
            while len(self._frames) >= MAX_QUEUED_FRAMES and not self._closed and not self._ended:
                remaining = until - time.monotonic()
                if remaining <= 0:
                    raise ValueError("talk producer backlog exceeded")
                self._condition.wait(remaining)
            if self._closed or self._ended or self._received >= MAX_AUDIO_FRAMES:
                raise ValueError("talk input ended or duration exceeded")
            self._frames.append(frame)
            self._received += 1
            self._last_input = self._clock()

    def take_burst(self) -> tuple[bytes, ...]:
        """Account reader: nonblocking, four frames or the explicitly finished tail."""
        with self._condition:
            if len(self._frames) < 4 and not self._ended:
                return ()
            result = tuple(self._frames.popleft() for _ in range(min(4, len(self._frames))))
            self._condition.notify_all()
            return result

    def finish(self) -> None:
        with self._condition:
            self._ended = True
            self._condition.notify_all()

    def cancel(self) -> None:
        with self._condition:
            self._aborted = self._ended = True
            self._frames.clear()
            self._condition.notify_all()

    def close(self) -> None:
        with self._condition:
            self._closed = self._ended = True
            self._frames.clear()
            self._condition.notify_all()

    def expire_producer(self) -> None:
        """Idle/slow input ends only this utterance, before the AV talk deadline."""
        with self._condition:
            now = self._clock()
            if not self._ended and (now - self._last_input >= 2 or now - self._created >= MAX_INPUT_SECONDS):
                self._aborted = self._ended = True
                self._frames.clear()
                self._condition.notify_all()


TalkFrames = tuple[bytes, ...] | StreamingTalkInput | LegacyTalkInput


def validate_talk_frames(frames: TalkFrames) -> None:
    if isinstance(frames, LegacyTalkInput):
        return  # Immutable and validated at construction; finite review only.
    if isinstance(frames, StreamingTalkInput):
        if frames.aborted:
            raise ValueError("talk input is cancelled")
        return
    if not isinstance(frames, tuple) or any(not isinstance(f, bytes) for f in frames):
        raise ValueError("immutable talk frames required")
    validate_amr_audio_frames(frames)
