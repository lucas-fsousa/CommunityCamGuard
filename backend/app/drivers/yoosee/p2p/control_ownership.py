"""Serialize broker-using control operations, including the PTZ release boundary."""
from __future__ import annotations

import threading
from collections.abc import Iterator
from contextlib import contextmanager

from .contracts import P2PProbeError

_channel = threading.RLock()
WAIT_SECONDS = 15.0


class ControlChannelBusy(P2PProbeError):
    """No channel ownership was obtained; no operation was started."""


@contextmanager
def own_control_channel() -> Iterator[None]:
    """No retry: another login must not invalidate a route between START and STOP.

    Reentrant because native PTZ owns the channel across its one-shot preparation
    helper and subsequent motion. A conservative driver-wide lock also covers
    refreshed access IDs; it is not a queue of repeatable movement commands.
    """
    if not _channel.acquire(timeout=WAIT_SECONDS):
        raise ControlChannelBusy("another broker control operation is still running")
    try:
        yield
    finally:
        _channel.release()
