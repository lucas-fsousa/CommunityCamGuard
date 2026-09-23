"""Process-local bounds for derived archive work, never live/recording encoders."""

import threading
from collections.abc import Iterator
from contextlib import contextmanager

MAX_JOBS = 4  # One encoder plus at most three waiting lightweight job threads.
QUEUE_WAIT_SECONDS = 30
ENCODE_TIMEOUT_SECONDS = 600
_ENCODER = threading.Lock()


class PlaybackBusy(RuntimeError):
    """A bounded archive preparation queue cannot admit more work."""


@contextmanager
def encoder_slot() -> Iterator[None]:
    if not _ENCODER.acquire(timeout=QUEUE_WAIT_SECONDS):
        raise PlaybackBusy("playback encoder wait budget exhausted")
    try:
        yield
    finally:
        _ENCODER.release()
